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
      - name: ava
        description: "Chief of staff — sitreps, planning, fleet delegation."
        type: a2a
        url: ${AVA_URL:-http://ava:3008/a2a}
        auth: { scheme: apiKey, credentialsEnv: AVA_API_KEY }

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
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from a2a.client import (
    A2AClient,
    A2ADispatchError,
    ProgressCallback,
)
from agent.tracing import active_trace, propagation_headers

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
    type: str  # "a2a" | "openai"

    # Common
    url: str = ""

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
    name = raw.get("name")
    dtype = (raw.get("type") or "").lower()
    desc = (raw.get("description") or "").strip()
    if not name or not dtype or not desc:
        logger.warning(f"[delegates] skipping entry missing name/type/description: {raw!r}")
        return None
    if dtype not in ("a2a", "openai"):
        logger.warning(f"[delegates] {name}: unknown type {dtype!r}; skipping")
        return None

    url = _expand_env(str(raw.get("url", "")))
    if not url:
        logger.warning(f"[delegates] {name}: url required; skipping")
        return None

    common = dict(name=name, description=desc, type=dtype, url=url)

    if dtype == "a2a":
        auth = raw.get("auth") or {}
        scheme = auth.get("scheme")
        cred_env = auth.get("credentialsEnv")
        cred = os.environ.get(cred_env) if cred_env else None
        if cred_env and not cred:
            logger.warning(
                f"[delegates] {name}: auth env {cred_env!r} unset (unauthenticated)"
            )
        return Delegate(
            **common,
            auth_scheme=scheme,
            a2a_credential=cred,
            a2a_headers=dict(raw.get("headers", {})),
        )

    # type=openai
    model = raw.get("model")
    if not model:
        logger.warning(f"[delegates] {name}: openai delegate requires model; skipping")
        return None
    key_env = raw.get("api_key_env")
    api_key = os.environ.get(key_env) if key_env else None
    if key_env and not api_key:
        logger.warning(
            f"[delegates] {name}: api_key_env {key_env!r} unset (sending unauthenticated)"
        )
    return Delegate(
        **common,
        model=model,
        openai_api_key=api_key or "not-needed",
        system_prompt=raw.get("system_prompt"),
        max_tokens=int(raw.get("max_tokens", 400)),
        temperature=float(raw.get("temperature", 0.4)),
    )


def _config_changed(a: "Delegate", b: "Delegate") -> bool:
    """Did the dispatch-relevant config change between two registry
    entries with the same name? Lightweight comparison — auth headers
    are derived from credentialsEnv resolution at parse time, so
    comparing the resolved values catches credential rotations too.

    Description / system_prompt changes don't invalidate the health
    cache because they don't affect reachability, only LLM picks.
    """
    if a.type != b.type or a.url != b.url:
        return True
    if a.type == "a2a":
        return (
            a.auth_scheme != b.auth_scheme
            or a.a2a_credential != b.a2a_credential
            or a.a2a_headers != b.a2a_headers
        )
    if a.type == "openai":
        return a.model != b.model or a.openai_api_key != b.openai_api_key
    return False


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


async def dispatch(
    delegate: Delegate,
    query: str,
    *,
    timeout: float = 60.0,
    progress_callback: ProgressCallback | None = None,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
) -> str:
    """Dispatch `query` to the given delegate. For A2A delegates, prefer
    SSE streaming so the voice layer can narrate intermediate status via
    `progress_callback`. OpenAI delegates don't stream here (chat
    completion non-stream); they're usually fast enough not to need it.
    """
    if delegate.type == "a2a":
        return await _dispatch_a2a(
            delegate, query,
            timeout=timeout,
            progress_callback=progress_callback,
            push_notification_url=push_notification_url,
            push_notification_token=push_notification_token,
        )
    if delegate.type == "openai":
        return await _dispatch_openai(delegate, query, timeout=timeout)
    raise DelegateError(f"unknown delegate type {delegate.type!r}")


# Per-delegate A2AClient cache — reused across dispatches so the Agent Card
# is fetched once. Keyed by (url, sorted auth headers) so an auth/url change
# yields a fresh client. The dispatch path passes a FRESH contextId per call
# (one-shot delegate_to/delegate_async have no multi-turn intent); the D1
# orchestrate loop constructs its own per-run clients for sticky context.
_a2a_clients: dict[tuple, A2AClient] = {}


def _client_for(delegate: Delegate) -> A2AClient:
    key = (delegate.url, tuple(sorted(delegate.auth_headers().items())))
    client = _a2a_clients.get(key)
    if client is None:
        client = A2AClient(
            delegate.url,
            headers=delegate.auth_headers(),
            card_origin=delegate.origin(),
            name=delegate.name,
        )
        _a2a_clients[key] = client
    return client


async def _dispatch_a2a(
    delegate: Delegate,
    query: str,
    *,
    timeout: float,
    progress_callback: ProgressCallback | None = None,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
) -> str:
    """Reliable synchronous `message/send` by default; opt into streaming
    (progress narration) with ``A2A_STREAM=1``.

    Streaming HANGS against servers that send their reply but never emit a
    recognized terminal SSE event (no ``final: true`` / terminal status) —
    the client waits for more lines until the httpx read timeout, and that
    ``TimeoutException`` isn't an ``A2ADispatchError`` so the fallback never
    fired and delegate_to looked dead. A2A push-back can't reach a 127.0.0.1
    desktop app either, so the streaming path buys little here. Default to
    sync; bound the opt-in stream with ``asyncio.wait_for``
    (``A2A_STREAM_TIMEOUT``, default 20s) and fall back to sync on any
    failure or timeout.

    Streaming is used when EITHER ``A2A_STREAM=1`` (manual opt-in) OR a
    ``progress_callback`` is supplied AND the delegate's card advertises
    ``streaming`` — so a caller that wants the delegate's intermediate status
    (for the visual rail) gets it when the agent actually supports it. The
    hang-guard differs: the manual env path keeps the short ``A2A_STREAM_TIMEOUT``
    distrust bound; a card-advertised status stream is trusted up to the full
    request ``timeout`` (a slow-but-streaming agent like Ava is not a hang).
    Any stream error/timeout falls back to one synchronous ``message/send``.
    """
    client = _client_for(delegate)
    ctx = uuid.uuid4().hex  # fresh per dispatch — one-shot, no multi-turn
    env_stream = os.environ.get("A2A_STREAM", "0") == "1"
    want_status = progress_callback is not None and await client.supports_streaming()
    if env_stream or want_status:
        # Trust a card-advertised status stream up to the full timeout; keep
        # the short distrust bound only for the manual A2A_STREAM opt-in.
        bound = (
            float(os.environ.get("A2A_STREAM_TIMEOUT", "20")) if env_stream
            else timeout
        )
        try:
            res = await asyncio.wait_for(
                client.send(
                    query,
                    context_id=ctx,
                    progress_callback=progress_callback,
                    prefer_stream=True,
                    timeout=timeout,
                    push_notification_url=push_notification_url,
                    push_notification_token=push_notification_token,
                ),
                timeout=bound,
            )
            return res.text
        except (A2ADispatchError, asyncio.TimeoutError, httpx.TimeoutException) as e:
            logger.warning(
                f"[delegates] {delegate.name} streaming failed/timed out ({e}); "
                "falling back to message/send"
            )
    res = await client.send(query, context_id=ctx, prefer_stream=False, timeout=timeout)
    return res.text


async def _dispatch_openai(delegate: Delegate, query: str, *, timeout: float) -> str:
    """One-shot non-streaming chat completion via plain httpx.

    We deliberately avoid the OpenAI SDK here — it adds `x-stainless-*`
    fingerprint headers + a `user-agent: AsyncOpenAI/…` string that some
    proxies (workstacean's WAF being the reason we found out) block. The
    endpoint contract is simple enough that raw httpx is cleaner.
    """
    sys_prompt = delegate.system_prompt or (
        "You are a research assistant. Answer thoroughly but concisely "
        "(2-4 sentences). Plain text only — no markdown, no lists. "
        "The answer will be spoken aloud verbatim."
    )
    headers = {"Content-Type": "application/json"}
    # Cross-fleet trace propagation — harmless on vanilla OpenAI but
    # stitches our trace if the endpoint is a fleet peer that speaks
    # the contract (see docs/reference/tracing-contract.md).
    headers.update(propagation_headers(trace=active_trace()))
    if delegate.openai_api_key and delegate.openai_api_key != "not-needed":
        # Both standard (Authorization: Bearer) and workstacean-style
        # (X-API-Key) are accepted by the servers we target today.
        # Bearer is the OpenAI contract; sticking with that.
        headers["Authorization"] = f"Bearer {delegate.openai_api_key}"
    payload = {
        "model": delegate.model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": query},
        ],
        "max_tokens": delegate.max_tokens,
        "temperature": delegate.temperature,
        "stream": False,
        # vLLM-hosted Qwen3.5/3.6 models emit reasoning into a separate
        # field unless this is off. Harmless for gateways that ignore it.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{delegate.url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
    except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
        raise DelegateError(f"{delegate.name} unreachable: {e}") from e
    except Exception as e:
        raise DelegateError(f"{delegate.name}: {e}") from e

    if r.status_code != 200:
        raise DelegateError(
            f"{delegate.name}: HTTP {r.status_code} — {r.text[:200]}"
        )
    try:
        body = r.json()
        return (body["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        raise DelegateError(f"{delegate.name}: malformed response ({e})") from e


# ---------------------------------------------------------------------------
# Reachability probe — used by /api/delegates/test + the background loop.
# ---------------------------------------------------------------------------


async def probe(delegate: Delegate, *, timeout: float = 8.0) -> dict:
    """Cheap reachability check for a delegate.

    For ``a2a`` delegates: GET ``{origin}/.well-known/agent-card.json``
    using the configured auth header. The card is an A2A spec
    requirement (the same endpoint our own ``a2a/server.py`` exposes
    at line 124-156), so it's the canonical "is this thing alive"
    surface. 401/403 reported distinctly from "unreachable" so the
    UI can show "your key is wrong" vs "delegate is down."

    For ``openai`` delegates: a one-shot ``/chat/completions`` ping
    via ``agent.llm_probe.ping_endpoint``, same as
    ``/api/llm/test``. Error shapes line up with the LLM probe so
    the Settings panel can render results identically across surfaces.

    Returns ``{ok: bool, latency_ms?: int, error?: str, status?: int}``.
    Never raises — every failure mode comes back as ``ok: False``.
    """
    if delegate.type == "a2a":
        origin = delegate.origin()
        if not origin:
            return {"ok": False, "error": f"malformed url: {delegate.url!r}"}
        card_url = f"{origin}/.well-known/agent-card.json"
        headers = delegate.auth_headers()
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(card_url, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            return {"ok": False, "error": f"unreachable: {e}"}
        except Exception as e:  # noqa: BLE001 — surface but don't raise
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        latency_ms = int((time.monotonic() - t0) * 1000)
        if r.status_code in (401, 403):
            return {
                "ok": False, "error": "auth rejected",
                "status": r.status_code, "latency_ms": latency_ms,
            }
        if r.status_code != 200:
            return {
                "ok": False,
                "error": f"agent card HTTP {r.status_code}",
                "status": r.status_code, "latency_ms": latency_ms,
            }
        return {"ok": True, "latency_ms": latency_ms}

    if delegate.type == "openai":
        from agent.llm_probe import ping_endpoint
        return await ping_endpoint(
            url=delegate.url,
            model=delegate.model or "",
            api_key=delegate.openai_api_key
            if delegate.openai_api_key and delegate.openai_api_key != "not-needed"
            else "",
        )

    return {"ok": False, "error": f"unknown delegate type {delegate.type!r}"}


# Default cadence — measured in seconds. Faster than the 30s personality
# poll (delegates can drop independently of the LLM stack the SPA already
# polls) but slow enough that a network blip doesn't burn through 12 probes
# in a minute. Override per-deployment via DELEGATE_HEALTH_INTERVAL.
_HEALTH_INTERVAL_SECS = float(
    os.environ.get("DELEGATE_HEALTH_INTERVAL", "300")
)
# Defer the FIRST probe by this many seconds after boot so the LLM model
# loads + the SPA hands shake without a stampede of probes competing for
# the event loop on a cold start.
_HEALTH_INITIAL_DELAY_SECS = float(
    os.environ.get("DELEGATE_HEALTH_INITIAL_DELAY", "30")
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
