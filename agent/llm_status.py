"""In-memory LLM "voice doctor" status.

Answers, without reading the sidecar log: which LLM is *configured*, which one
is actually *running*, did the last request work, and has the live service
drifted from what the persona config says (e.g. a hot-swap that silently failed
and left the old model serving turns).

Populated from three existing seams — the LatencyObserver ttfb path, the
LLMErrorAnnouncer error hook, and the failover callback in voice/pipeline.py —
so there is no new polling. Exposed three ways:

* ``GET /api/voice/doctor`` (authenticated): full detail incl. the URL — never
  the API key (it is never stored here).
* ``/healthz.voice.llm`` (unauthenticated): host-only copy.
* ``llm-status`` SSE event (retained, host-only), published on change.

Drift compares ``(host, model)`` only: provider strings are not comparable
between persona config ("anthropic-oauth") and a live service class, and the
host already implies the wire protocol. A side that is unknown never counts as
drift (no false alarms before a session exists).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from voice.sse_bus import SseBus, sse_bus

_MAX_MSG = 200
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]{8,}|npm_[A-Za-z0-9]{8,})"
)


def url_host(url: Any) -> str | None:
    """``host[:port]`` of a URL — no scheme, credentials, path or query."""
    if not url:
        return None
    try:
        parts = urlsplit(str(url) if "://" in str(url) else f"//{url}")
        return parts.netloc.rsplit("@", 1)[-1] or None
    except ValueError:
        return None


def safe_url(url: Any) -> str | None:
    """URL with credentials and query stripped (query strings can carry keys)."""
    if not url:
        return None
    try:
        p = urlsplit(str(url))
        host = p.netloc.rsplit("@", 1)[-1]
        if not p.scheme or not host:
            return url_host(url)
        return f"{p.scheme}://{host}{p.path}"
    except ValueError:
        return None


def scrub(msg: Any) -> str:
    """Single-line, secret-scrubbed, length-capped error text."""
    text = " ".join(str(msg).split())
    return _SECRET_RE.sub("[redacted]", text)[:_MAX_MSG]


def describe_service(svc: Any) -> dict:
    """Best-effort ``{class, url, host, model}`` read from a live LLM service."""
    url = None
    for getter in (
        lambda s: s._client.base_url,  # OpenAI-compat (AsyncOpenAI)
        lambda s: s._ollama_root,  # native Ollama adapter
        lambda s: s._base_url,
    ):
        try:
            val = getter(svc)
        except Exception:  # noqa: BLE001 — attribute shapes vary per adapter
            continue
        if val:
            url = str(val)
            break
    model = None
    try:
        model = getattr(svc._settings, "model", None)
    except Exception:  # noqa: BLE001
        pass
    return {
        "class": type(svc).__name__,
        "url": safe_url(url),
        "host": url_host(url),
        "model": str(model) if model else None,
    }


def configured_from_llm_block(
    llm: dict | None,
    *,
    default_url: str,
    default_model: str,
    config_mtime: float | None,
) -> dict:
    """Configured LLM as the persona resolves it (same precedence as
    ``_resolve_skill_llm``: persona.llm.* → env-derived defaults)."""
    llm = llm or {}
    url = str(llm.get("url") or default_url)
    return {
        "provider": llm.get("provider") or None,
        "url": safe_url(url),
        "host": url_host(url),
        "model": str(llm.get("model") or default_model),
        "extra_models": [
            str(m) for m in (llm.get("router_model"), llm.get("content_model")) if m
        ],
        "config_mtime": config_mtime,
    }


class LLMStatus:
    def __init__(self, bus: SseBus = sse_bus) -> None:
        self._bus = bus
        self._members: list[Any] = []
        self._active_idx = 0
        self._configured: dict | None = None
        self._configured_resolver: Callable[[], dict] | None = None
        self.last_ok_at: float | None = None
        self.last_ttfb_ms: int | None = None
        self.last_error: dict | None = None
        self.failovers = 0
        self._last_published: dict | None = None

    # -- wiring -----------------------------------------------------------

    def reset(self) -> None:
        self.__init__(self._bus)  # type: ignore[misc]

    def bind(
        self, members: list[Any], resolver: Callable[[], dict] | None = None
    ) -> None:
        """Called per pipeline build: the live LLM members (primary first)."""
        self._members = list(members)
        self._active_idx = 0
        if resolver is not None:
            self._configured_resolver = resolver
        self._notify()

    def set_configured_resolver(self, resolver: Callable[[], dict]) -> None:
        self._configured_resolver = resolver

    # -- recording --------------------------------------------------------

    def record_ttfb(self, seconds: float) -> None:
        self.last_ttfb_ms = int(seconds * 1000)
        self.last_ok_at = time.time()
        self._notify()

    def record_error(
        self,
        error_class: str,
        msg: Any,
        *,
        status: int | None = None,
        exception: BaseException | None = None,
    ) -> None:
        if status is None:
            status = getattr(exception, "status_code", None)
        self.last_error = {
            "at": time.time(),
            "class": error_class,
            "status": status,
            "msg": scrub(msg),
        }
        self._notify()

    def record_failover(self, idx: int) -> None:
        self.failovers += 1
        self._active_idx = max(idx, 0)
        self._notify()

    # -- views ------------------------------------------------------------

    def running(self) -> dict | None:
        if not self._members:
            return None
        idx = min(self._active_idx, len(self._members) - 1)
        info = describe_service(self._members[idx])
        info["member"] = "primary" if idx == 0 else "backup"
        return info

    def configured(self) -> dict | None:
        if self._configured_resolver is not None:
            try:
                self._configured = self._configured_resolver()
            except Exception:  # noqa: BLE001 — doctor must not crash on bad config
                pass
        return self._configured

    @staticmethod
    def compute_drift(configured: dict | None, running: dict | None) -> bool:
        if not configured or not running:
            return False
        # A failed-over backup legitimately differs from the configured primary.
        if running.get("member") == "backup":
            return False
        models = {configured.get("model"), *configured.get("extra_models", [])}
        r_model = running.get("model")
        if r_model and configured.get("model") and r_model not in models:
            return True
        c_host, r_host = configured.get("host"), running.get("host")
        return bool(c_host and r_host and c_host != r_host)

    def snapshot(self, *, public: bool = False) -> dict:
        configured, running = self.configured(), self.running()
        drift = self.compute_drift(configured, running)

        def _view(d: dict | None, keys: tuple[str, ...]) -> dict | None:
            return None if d is None else {k: d.get(k) for k in keys}

        full = not public
        err = self.last_error
        return {
            "configured": _view(
                configured,
                ("provider", "host", "model", "config_mtime")
                + (("url",) if full else ()),
            ),
            "running": _view(
                running,
                ("class", "host", "model", "member") + (("url",) if full else ()),
            ),
            "last_ok_at": self.last_ok_at,
            "last_ttfb_ms": self.last_ttfb_ms,
            # The message can echo provider text; keep it off the public copy.
            "last_error": (
                None
                if err is None
                else {k: v for k, v in err.items() if full or k != "msg"}
            ),
            "failovers": self.failovers,
            "drift": drift,
        }

    def brief(self) -> dict:
        """``{model, member, host}`` for the per-turn summary line."""
        r = self.running() or {}
        return {k: r.get(k) for k in ("model", "member", "host")}

    # -- SSE --------------------------------------------------------------

    def _notify(self) -> None:
        snap = self.snapshot(public=True)
        if snap == self._last_published:
            return
        self._last_published = snap
        try:
            asyncio.get_running_loop().create_task(
                self._bus.publish("llm-status", snap, retain=True)
            )
        except RuntimeError:
            pass  # no loop (sync context / tests): nothing to fan out to


llm_status = LLMStatus()


def configured_now() -> dict:
    """Configured LLM from the live persona config (resolved fresh per call, so
    an edited orbis.yaml shows up as drift until the running service catches up).
    Reads the same precedence as ``_resolve_skill_llm`` but without its side
    effects (placeholder warnings, key resolution) — this runs on every poll."""
    import os
    from pathlib import Path

    import app  # lazy: app imports the routers that import this module
    from agent.config_store import DEFAULT_PATH
    from agent.persona import get_active_persona

    try:
        mtime: float | None = Path(DEFAULT_PATH).expanduser().stat().st_mtime
    except OSError:
        mtime = None
    return configured_from_llm_block(
        get_active_persona().llm,
        default_url=app.LLM_URL,
        default_model=app.LLM_SERVED_NAME or os.environ.get("LLM_SERVED_NAME", "local"),
        config_mtime=mtime,
    )
