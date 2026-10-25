"""Boot banner: which config file is live, and which fields fell back to env.

Config precedence is yaml-wins with env as a fallback only (DECISIONS
2026-07-15), so "why is it talking to localhost:8100?" is almost always "yaml
had no value and an env var / built-in default filled in". One line at sidecar
start, and the same dict in /api/diagnostics, makes that visible without
spelunking. Never includes values of secrets — only field names.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# (dotted yaml path, env var that fills it when the yaml field is empty)
_ENV_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("llm.url", "LLM_URL"),
    ("llm.model", "LLM_SERVED_NAME"),
    ("llm.api_key", "LLM_API_KEY"),
    ("voice.tts_backend", "TTS_BACKEND"),
    ("stt.backend", "STT_BACKEND"),
)


def _dig(cfg: dict, dotted: str) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def env_fallbacks_used(cfg: dict, environ: dict | None = None) -> list[str]:
    """Names (``llm.url<-LLM_URL``) of fields the yaml left empty while the
    matching env var is set — i.e. the env is what's actually in effect."""
    environ = os.environ if environ is None else environ
    used = []
    for field, env in _ENV_FALLBACKS:
        if not environ.get(env) or _dig(cfg, field):
            continue
        # api_key_env is the yaml's own indirection for the key.
        if field == "llm.api_key" and _dig(cfg, "llm.api_key_env"):
            continue
        used.append(f"{field}<-{env}")
    return used


def config_banner() -> dict:
    from agent.config_store import DEFAULT_PATH, read_config

    path = Path(DEFAULT_PATH).expanduser()
    try:
        mtime_epoch: float | None = path.stat().st_mtime
    except OSError:
        mtime_epoch = None
    try:
        import importlib.metadata as _md

        version = _md.version("orbis")
    except Exception:  # noqa: BLE001
        version = "unknown"
    try:
        fallbacks = env_fallbacks_used(read_config())
    except Exception:  # noqa: BLE001 — banner must never block boot
        fallbacks = []
    return {
        "path": str(path.resolve()) if mtime_epoch is not None else str(path),
        "exists": mtime_epoch is not None,
        "mtime": (
            datetime.fromtimestamp(mtime_epoch, timezone.utc).isoformat(
                timespec="seconds"
            )
            if mtime_epoch is not None
            else None
        ),
        "env_fallbacks_used": fallbacks,
        "version": version,
    }


def log_config_banner() -> dict:
    b = config_banner()
    logger.info(
        "[config] path=%s mtime=%s env_fallbacks_used=%s version=%s",
        b["path"],
        b["mtime"] or "missing",
        b["env_fallbacks_used"],
        b["version"],
    )
    return b


class StageTimer:
    """Collects per-stage wall seconds for the prewarm log lines."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self.stages: dict[str, float] = {}

    def record(self, stage: str, started: float) -> None:
        secs = time.monotonic() - started
        self.stages[stage] = secs
        logger.info("[prewarm] %s took %.2fs", stage, secs)

    def log_total(self) -> None:
        logger.info(
            "[prewarm] total %.2fs (%s)",
            time.monotonic() - self._t0,
            ", ".join(f"{k}={v:.2f}s" for k, v in self.stages.items()) or "no stages",
        )
