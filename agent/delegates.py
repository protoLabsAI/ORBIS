"""Unified delegate registry + reachability probe.

The agent has ONE tool — `delegate_to(target, query)` — for handing off
heavy questions to:

  - **A2A agents** in the protoLabs fleet (ava, quinn, etc.)
  - **OpenAI-compatible LLM endpoints** (gateway / cloud / self-hosted)

Each delegate has a name + human description; the LLM picks based on
the descriptions baked into the tool's schema. New delegates are added
by editing `config/delegates.yaml` — no code change needed.

Schema (config/delegates.yaml):

    delegates:
      - name: helm
        description: "Chief of staff — sitreps, planning, fleet delegation."
        type: a2a
        url: ${AGENT_URL:-http://agent:3008/a2a}
        auth: { scheme: apiKey, credentialsEnv: AGENT_API_KEY }

      - name: opus
        description: "Heavy reasoning model — analysis, summarization, depth."
        type: openai
        url: http://gateway:4000/v1
        model: claude-opus-4-6
        api_key_env: LITELLM_MASTER_KEY
        system_prompt: "Answer thoroughly but concisely (2-4 sentences)."
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:  # type-only — the runtime bodies live in delegate_adapters
    from a2a_outbound import A2AClient, ProgressCallback

logger = logging.getLogger(__name__)


# POSIX env substitution shared with the old AgentRegistry semantics.
_ENV_RE = re.compile(r"\$\{?([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}?")


def _expand_env(value: str) -> str:
    def repl(m: re.Match) -> str:
        name, default = m.group(1), m.group(2) or ""
        return os.environ.get(name, default)
    return _ENV_RE.sub(repl, value)


@dataclass
class Delegate:
    """A single dispatch target. Either an A2A agent or an OpenAI-compat
    LLM endpoint, switched on `type`."""
    name: str
    description: str
    type: str  # "a2a" | "openai" | "acp"

    # Common
    url: str = ""

    # type=acp — a local coding agent ORBIS launches + drives over ACP.
    command: str = ""  # the agent binary (e.g. "proto", "opencode")
    args: list[str] = field(default_factory=list)  # e.g. ["--acp"]
    workdir: str = ""  # the directory the agent is responsible for (session cwd)

    # type=a2a
    auth_scheme: str | None = None
    a2a_credential: str | None = None
    a2a_headers: dict[str, str] = field(default_factory=dict)

    # type=openai
    model: str | None = None
    openai_api_key: str | None = None
    system_prompt: str | None = None
    max_tokens: int = 400
    temperature: float = 0.4

    def origin(self) -> str:
        """Scheme+host+port of ``url`` (the JSON-RPC endpoint for a2a,
        the /v1 base for openai). Used by the probe to derive the agent
        card URL for a2a delegates without hard-coding the path."""
        from urllib.parse import urlparse
        parsed = urlparse(self.url)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"

    def auth_headers(self) -> dict[str, str]:
        """A2A auth header dict. Empty for openai delegates."""
        h: dict[str, str] = dict(self.a2a_headers)
        if self.type != "a2a" or not self.auth_scheme or not self.a2a_credential:
            return h
        if self.auth_scheme == "apiKey":
            h["X-API-Key"] = self.a2a_credential
        elif self.auth_scheme == "bearer":
            h["Authorization"] = f"Bearer {self.a2a_credential}"
        else:
            logger.warning(
                f"[delegates] unknown auth scheme {self.auth_scheme!r} for {self.name}"
            )
        return h


def _parse_entry(raw: dict) -> Delegate | None:
    """Build a ``Delegate`` from a raw YAML entry, routing the type-specific
    parse to the registered adapter. Returns ``None`` for a missing/unknown
    type or a malformed entry — the registry silently skips those. The
    per-type bodies live in ``agent/delegate_adapters.py``."""
    name = raw.get("name")
    dtype = (raw.get("type") or "").lower()
    desc = (raw.get("description") or "").strip()
    if not name or not dtype or not desc:
        logger.warning(f"[delegates] skipping entry missing name/type/description: {raw!r}")
        return None
    from agent.delegate_adapters import get_adapter
    try:
        adapter = get_adapter(dtype)
    except KeyError:
        logger.warning(f"[delegates] {name}: unknown type {dtype!r}; skipping")
        return None
    return adapter.parse(raw, name, desc)


def _config_changed(a: "Delegate", b: "Delegate") -> bool:
    """Did dispatch-relevant config change between two same-named entries
    (→ invalidate the health cache)? A type change always counts; the rest is
    the adapter's call (it compares url + its own auth/model fields). Auth
    headers are resolved at parse time, so comparing the resolved values
    catches credential rotations too. Description / system_prompt changes don't
    invalidate — they affect LLM picks, not reachability."""
    if a.type != b.type:
        return True
    from agent.delegate_adapters import get_adapter
    try:
        return get_adapter(a.type).config_changed(a, b)
    except KeyError:
        return True


@dataclass
class DelegateHealth:
    """Cached reachability snapshot for a single delegate.

    Populated by the background prober (``health_loop``); exposed via
    ``/healthz.delegates[]`` so the SPA can render a "delegate degraded"
    banner before the user tries to delegate to one that's down. The
    ``ok`` field is ``None`` until the first probe lands so the UI can
    distinguish "not yet checked" from "confirmed down".
    """
    ok: bool | None = None
    latency_ms: int | None = None
    # epoch seconds (time.time) so it round-trips cleanly through JSON
    last_checked: float | None = None
    last_error: str | None = None
    # Counter for "degraded" classification — a single transient failure
    # (network blip, DNS jitter) shouldn't trip the banner. Caller picks
    # the threshold; we just maintain the counter.
    consecutive_failures: int = 0


class DelegateRegistry:
    """Loads + indexes delegates from YAML."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._items: dict[str, Delegate] = {}
        # Reachability cache, keyed by delegate name. Populated by the
        # background prober; missing entry = "not probed yet".
        self._health: dict[str, DelegateHealth] = {}
        if self._path and self._path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = yaml.safe_load(self._path.read_text()) or {}
        except Exception as e:
            logger.error(f"[delegates] failed to read {self._path}: {e}")
            return
        for raw in data.get("delegates", []) or []:
            entry = _parse_entry(raw)
            if entry:
                self._items[entry.name] = entry
        logger.info(
            f"[delegates] loaded {len(self._items)}: "
            f"{[(d.name, d.type) for d in self._items.values()]}"
        )

    def get(self, name: str) -> Delegate | None:
        return self._items.get(name)

    def names(self) -> list[str]:
        return list(self._items.keys())

    def all(self) -> list[Delegate]:
        return list(self._items.values())

    def health(self, name: str) -> DelegateHealth | None:
        """Latest probe result for ``name`` or ``None`` if never probed."""
        return self._health.get(name)

    def all_health(self) -> dict[str, DelegateHealth]:
        """Snapshot of every probed delegate's health. Returns a copy
        so callers can't mutate the live cache."""
        return dict(self._health)

    def record_health(self, name: str, ok: bool, *,
                      latency_ms: int | None = None,
                      error: str | None = None) -> DelegateHealth:
        """Update the health cache for ``name``. Returns the new entry.

        Increments ``consecutive_failures`` on a fail; resets it on a
        pass. Caller (the prober) is responsible for actually deciding
        what "fail" means — we just bookkeep.
        """
        prev = self._health.get(name)
        consecutive = (prev.consecutive_failures if prev else 0) + 1 if not ok else 0
        entry = DelegateHealth(
            ok=ok,
            latency_ms=latency_ms,
            last_checked=time.time(),
            last_error=error,
            consecutive_failures=consecutive,
        )
        self._health[name] = entry
        return entry

    def reload(self) -> list[str]:
        """Re-read the YAML from disk and replace the in-memory index.
        Returns the list of delegate names after reload. Safe to call
        mid-session — delegate selection happens per-tool-call, so
        existing sessions pick up the new registry on their next
        delegate_to() invocation. No-op when this is a filtered copy
        (no _path set).

        Reloads also drop any cached health for delegates whose config
        changed (URL, auth, type) — a green cache from the *previous*
        config would otherwise lie about the new endpoint until the
        next background probe lands. Names that were removed get
        pruned. Names that survive unchanged keep their cached health
        so the SPA's banner doesn't flicker on every reload.
        """
        if not self._path:
            return list(self._items.keys())
        prev = self._items
        self._items = {}
        if self._path.exists():
            self._load()
        else:
            logger.warning(f"[delegates] reload: {self._path} missing, registry now empty")
        self._prune_stale_health(prev)
        return list(self._items.keys())

    def _prune_stale_health(self, prev: dict[str, Delegate]) -> None:
        """Drop ``_health`` entries for delegates that were removed OR
        whose dispatchable config changed. Called after each reload
        so the cache reflects only what's currently configured.
        """
        if not self._health:
            return
        valid: dict[str, DelegateHealth] = {}
        for name, h in self._health.items():
            new = self._items.get(name)
            old = prev.get(name)
            if new is None:
                continue  # delegate removed — drop its health
            if old is None or _config_changed(old, new):
                continue  # delegate added or reconfigured — force re-probe
            valid[name] = h
        if len(valid) != len(self._health):
            logger.info(
                f"[delegates] health cache pruned: "
                f"{len(self._health)} → {len(valid)} entries after reload"
            )
        self._health = valid

    def filtered(self, allow: list[str] | None) -> "DelegateRegistry":
        """Return a shallow-copy registry limited to the given names.

        `None` or empty list returns the full registry (current behavior).
        Unknown names are dropped silently — skills with typos don't lose
        access to all delegates, just the mistyped ones.
        """
        if not allow:
            return self
        out = DelegateRegistry(None)
        for name in allow:
            d = self._items.get(name)
            if d:
                out._items[name] = d
        return out

    def __bool__(self) -> bool:
        return bool(self._items)


# ---------------------------------------------------------------------------
# Dispatch — single async fn that handles both delegate types.
# ---------------------------------------------------------------------------

class DelegateError(RuntimeError):
    """Raised on any dispatch failure. Caller speaks the message back to the user."""


def _count_dispatch(delegate: Delegate, status: str, secs: float) -> None:
    """Count one finished dispatch under its terminal status — ``ok`` /
    ``error`` / ``timeout`` / ``cancelled`` (#683 Phase E).

    The unlabelled ``delegate_dispatch_total`` / ``delegate_dispatch_errors``
    counters are compatibility aliases with the original semantics: total
    counts successes only, errors counts raised dispatches (timeouts
    included), and a cancelled dispatch — a barge-in — counts toward
    NEITHER, exactly as before (CancelledError bypassed ``except
    Exception``). The latency gauge follows the same rule: a barge-in
    abort lands in well under a second, so writing it would clobber the
    last meaningful round-trip time with noise."""
    from agent import metrics
    metrics.inc(f"delegate_dispatch:{status}")
    metrics.inc(f"delegate_dispatch:{delegate.name}:{status}")
    if status == "ok":
        metrics.inc("delegate_dispatch_total")
        metrics.inc(f"delegate_dispatch_total:{delegate.name}")
    elif status in ("error", "timeout"):
        metrics.inc("delegate_dispatch_errors")
        metrics.inc(f"delegate_dispatch_errors:{delegate.name}")
    if status == "cancelled":
        metrics.inc("delegate_barge_drop_total")
        metrics.inc(f"delegate_barge_drop_total:{delegate.name}")
        return
    ms = int(secs * 1000)
    metrics.set("delegate_dispatch_latency_ms", ms)
    metrics.set(f"delegate_dispatch_latency_ms:{delegate.name}", ms)


async def dispatch(
    delegate: Delegate,
    query: str,
    *,
    timeout: float = 60.0,
    progress_callback: ProgressCallback | None = None,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
) -> str:
    """Dispatch ``query`` to ``delegate``, routing to the registered adapter.
    The per-type dispatch bodies (a2a streaming/sync, openai chat-completion,
    acp stdio) live in ``agent/delegate_adapters.py``.

    This is the single observability chokepoint for outbound delegation:
    every dispatch gets a ``delegate.<type>`` span, per-status counters
    (ok / error / timeout / cancelled — see ``_count_dispatch``), and a
    greppable ``[delegate]`` latency line — the audited gap was that a 90s
    hand-off returning junk was only diagnosable by vibes."""
    from a2a_outbound import A2ADispatchTimeout
    from agent import tracing
    from agent.delegate_adapters import get_adapter
    try:
        adapter = get_adapter(delegate.type)
    except KeyError:
        raise DelegateError(f"unknown delegate type {delegate.type!r}")
    t0 = time.monotonic()
    with tracing.span(
        f"delegate.{delegate.type}",
        as_type="tool",
        input={"delegate": delegate.name, "query": query[:400]},
    ) as sp:
        try:
            result = await adapter.dispatch(
                delegate, query,
                timeout=timeout,
                progress_callback=progress_callback,
                push_notification_url=push_notification_url,
                push_notification_token=push_notification_token,
            )
        except asyncio.CancelledError:
            # Barge-in / caller bailed. Count the drop, but never as an
            # error and never into the latency gauge (see _count_dispatch).
            secs = time.monotonic() - t0
            _count_dispatch(delegate, "cancelled", secs)
            logger.info(
                f"[delegate] dispatch name={delegate.name} type={delegate.type} "
                f"ok=false secs={secs:.2f} cancelled"
            )
            try:
                sp.update(level="WARNING", status_message="cancelled")
            except Exception:  # noqa: BLE001
                pass
            raise
        except Exception as e:
            secs = time.monotonic() - t0
            status = (
                "timeout"
                if isinstance(e, (A2ADispatchTimeout, asyncio.TimeoutError))
                else "error"
            )
            _count_dispatch(delegate, status, secs)
            logger.warning(
                f"[delegate] dispatch name={delegate.name} type={delegate.type} "
                f"ok=false secs={secs:.2f} error={type(e).__name__}: {e}"
            )
            try:
                sp.update(level="ERROR", status_message=str(e)[:300])
            except Exception:  # noqa: BLE001
                pass
            raise
        secs = time.monotonic() - t0
        _count_dispatch(delegate, "ok", secs)
        logger.info(
            f"[delegate] dispatch name={delegate.name} type={delegate.type} "
            f"ok=true secs={secs:.2f} chars={len(result)}"
        )
        try:
            sp.update(output=result[:400])
        except Exception:  # noqa: BLE001
            pass
        return result


def _client_for(delegate: Delegate) -> A2AClient:
    """The cached A2AClient for an a2a delegate. Kept as a module facade because
    the D1 orchestrate loop imports it (``client_for=_client_for``) to share the
    Agent-Card cache; the cache itself now lives on the A2A adapter."""
    from agent.delegate_adapters import get_adapter
    return get_adapter("a2a").client_for(delegate)


# ---------------------------------------------------------------------------
# Reachability probe — used by /api/delegates/test + the background loop.
# ---------------------------------------------------------------------------


async def probe(delegate: Delegate, *, timeout: float = 8.0) -> dict:
    """Cheap reachability check for a delegate, routed to the registered
    adapter. Returns ``{ok: bool, latency_ms?: int, error?: str, status?: int}``
    and never raises — every failure mode comes back as ``ok: False``. The
    per-type probes (a2a agent-card GET, openai /chat/completions ping, acp
    PATH+workdir check) live in ``agent/delegate_adapters.py``."""
    from agent.delegate_adapters import get_adapter
    try:
        adapter = get_adapter(delegate.type)
    except KeyError:
        return {"ok": False, "error": f"unknown delegate type {delegate.type!r}"}
    return await adapter.probe(delegate, timeout=timeout)


# Default cadence — measured in seconds. Faster than the 30s personality
# poll (delegates can drop independently of the LLM stack the SPA already
# polls) but slow enough that a network blip doesn't burn through 12 probes
# in a minute. Override per-deployment via DELEGATE_HEALTH_INTERVAL.
_HEALTH_INTERVAL_SECS = float(
    os.environ.get("DELEGATE_HEALTH_INTERVAL", "300")
)
# The first probe starts immediately, but health_loop itself is a background
# task and every network operation is async, so it never holds the boot gate.
# This lets the ready-stage UI surface a confirmed-down local hub before the
# user's first delegation. Operators with a large remote fleet can restore a
# grace period through the environment variable.
_HEALTH_INITIAL_DELAY_SECS = float(
    os.environ.get("DELEGATE_HEALTH_INITIAL_DELAY", "0")
)

# Per-delegate fast-retry steps (seconds) for the first N consecutive
# failures, before settling into the base interval. Picks up a recovery
# inside ~30s instead of waiting the full 5-minute base cadence — the
# audit's "marks degraded and stays there until next cycle" gap.
# Tuple length defines how many fast retries before backing off to base.
_RETRY_STEPS_SECS: tuple[float, ...] = (30.0, 60.0, 120.0)

# Loop poll cadence — how often the outer loop wakes to check which
# delegates are due. Must be ≤ the smallest retry step so a fast retry
# fires on time. Cap at the base interval too — if a deployment sets
# a tiny DELEGATE_HEALTH_INTERVAL, we'd prefer a tighter tick than
# this hard 10s ceiling.
_HEALTH_TICK_SECS = 10.0


def _next_probe_delay(consecutive_failures: int, base_interval: float) -> float:
    """How long to wait before re-probing this delegate.

    Healthy delegates get the base interval with ±10% jitter (so a
    fleet of delegates doesn't all probe at the same instant against
    a shared backend). Failing delegates get a short fast-retry step
    so transient blips don't park the UI banner red for 5 minutes.
    Beyond ``_RETRY_STEPS_SECS`` consecutive failures, fall back to
    the jittered base interval — the service is genuinely down and
    hammering it on a fast retry buys nothing.
    """
    if consecutive_failures > 0:
        idx = consecutive_failures - 1
        if idx < len(_RETRY_STEPS_SECS):
            return _RETRY_STEPS_SECS[idx]
    return base_interval * random.uniform(0.9, 1.1)


async def health_loop(
    registry: "DelegateRegistry",
    *,
    interval_secs: float | None = None,
    initial_delay_secs: float | None = None,
) -> None:
    """Background task: probe every delegate periodically and update
    the registry's health cache.

    Started by ``app.py``'s lifespan handler. Cancelled cleanly on
    shutdown. Never raises out of the loop — a transient probe failure
    is the *expected* signal we're trying to capture, and an
    unexpected exception (e.g. from a bad delegate config) gets logged
    and swallowed so the loop stays alive for the other delegates.

    Scheduling is per-delegate: each delegate carries a ``next_due``
    timestamp. Healthy delegates are probed every ``interval_secs``
    (with ±10% jitter to avoid stampedes); failing delegates use
    ``_RETRY_STEPS_SECS`` for the first few consecutive failures so
    a quick recovery is picked up inside ~30s. The outer loop wakes
    on ``_HEALTH_TICK_SECS`` to check which delegates are due.
    """
    iv = interval_secs if interval_secs is not None else _HEALTH_INTERVAL_SECS
    delay = (
        initial_delay_secs if initial_delay_secs is not None
        else _HEALTH_INITIAL_DELAY_SECS
    )
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return

    # Per-delegate next-probe deadlines (monotonic seconds). Missing key
    # means "due now"; newly added delegates land here on their first
    # registry.all() iteration and get probed immediately.
    next_due: dict[str, float] = {}
    # Outer poll cadence — capped by the base interval so tiny intervals
    # in tests don't stretch beyond the test's expected tick.
    tick = min(_HEALTH_TICK_SECS, iv)

    while True:
        try:
            now = time.monotonic()
            for delegate in registry.all():
                if next_due.get(delegate.name, 0.0) > now:
                    continue  # not due yet — wait for the deadline
                try:
                    result = await probe(delegate)
                except Exception as e:  # noqa: BLE001 — defensive
                    logger.exception(
                        f"[delegate-health] probe of {delegate.name} crashed: {e}"
                    )
                    registry.record_health(
                        delegate.name, ok=False, error=str(e),
                    )
                    from agent import metrics
                    metrics.inc("delegate_probe_crashed")
                    h = registry.health(delegate.name)
                    consecutive = h.consecutive_failures if h else 1
                    next_due[delegate.name] = now + _next_probe_delay(consecutive, iv)
                    continue
                ok = bool(result.get("ok"))
                registry.record_health(
                    delegate.name,
                    ok=ok,
                    latency_ms=result.get("latency_ms"),
                    error=result.get("error"),
                )
                if not ok:
                    from agent import metrics
                    metrics.inc("delegate_probe_failed")
                h = registry.health(delegate.name)
                consecutive = h.consecutive_failures if h else 0
                next_due[delegate.name] = now + _next_probe_delay(consecutive, iv)
                if not ok and h and h.consecutive_failures > 1:
                    # Keep the noise gradient sensible: first failure
                    # is INFO (might be transient), repeated is WARNING.
                    logger.warning(
                        f"[delegate-health] {delegate.name} degraded "
                        f"({h.consecutive_failures} consecutive): "
                        f"{result.get('error')}"
                    )
        except Exception as e:  # noqa: BLE001 — never let the loop die
            logger.exception(f"[delegate-health] loop iteration crashed: {e}")
        try:
            await asyncio.sleep(tick)
        except asyncio.CancelledError:
            return
