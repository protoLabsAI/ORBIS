"""System / diagnostics routes: health, SSE, metrics, diagnostics, identity.

Extracted from app.py (#app.py-decomposition). These handlers reference a lot
of process-wide state that lives in ``app`` — the module stays the single home
for that state so there's exactly one source of truth. Names that tests
monkeypatch or that are reassigned at runtime (``audio_runtime_info``,
``_native_transport``, ``_native_pipeline_task``, ``user_state_for``,
``_DELEGATES``) are read as ``app.<name>`` at call time so the live value (and
test patches) win; stable helpers/constants are imported by value below.
"""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends

import app
from agent.echo_guard import ECHO_GUARD_MS, HALF_DUPLEX
from agent.persona import get_active_persona
from agent.user_state import active_user_states
from auth import require_user, user_registry
from auth.users import User
from voice.sse_bus import sse_bus
from voice.runtime_config import resolve_speech_config
from app import (
    NOISE_FILTER,
    SMART_TURN,
    _delegate_health_payload,
    _METRICS,
)

router = APIRouter()


@router.get("/healthz")
async def health():
    """Public — no auth. Reports process-wide shape, not per-user state."""
    stt_config, tts_config = resolve_speech_config(get_active_persona())
    return {
        "status": "ok",
        "stt_backend": stt_config["backend"],
        "tts_backend": tts_config["backend"],
        "auth_source": user_registry.source,
        "owner_configured": not user_registry.single_user_mode(),
        "active_sessions": len(active_user_states()),
        "delegates": [
            _delegate_health_payload(d, public=True) for d in app._DELEGATES.all()
        ],
        "persona": get_active_persona().slug,
        "voice": {"lifecycle": app._voice_lifecycle.snapshot()},
        "audio": {
            "transport": "native",
            **app.audio_runtime_info(),
            "socket_configured": bool(os.environ.get("ORBIS_AUDIO_SOCK")),
            "socket_connected": bool(
                app._native_transport and app._native_transport.connected
            ),
            "mic_frames_received": (
                app._native_transport.mic_frames_received
                if app._native_transport
                else 0
            ),
            "speaker_frames_sent": (
                app._native_transport.speaker_frames_sent
                if app._native_transport
                else 0
            ),
            "pipeline_running": bool(
                app._voice_lifecycle.is_running()
                and app._native_transport
                and app._native_transport.connected
                and app._native_pipeline_task
                and not app._native_pipeline_task.done()
            ),
            "half_duplex": HALF_DUPLEX,
            "echo_guard_ms": ECHO_GUARD_MS,
            "noise_filter": NOISE_FILTER,
            "smart_turn": SMART_TURN,
        },
    }


@router.get("/api/events")
async def events(user: User = Depends(require_user)):
    """Server-Sent Events stream of real-time bot state.

    The frontend (VoiceStateBridge) subscribes here so it can animate
    the orb and update the status pill. Drives the native audio path —
    WebRTC was removed in DECISIONS.md amendment 2026-04-28.

    Events emitted:
        bot-state   {"state": "idle"|"listening"|"thinking"|"speaking"}
        transcript  {"source": "user"|"bot", "text": "...", "final": true|false}
        session     {"event": "start"|"end", "session_id": "..."}
        delegate-health  {"name": "hub", "ok": bool, ...} at startup
        voice-lifecycle  {"state": "warming"|"starting"|"running"|"failed",
                          "detail": "...", "code"?: "...",
                          "action"?: "retry"|"relaunch_required"} (retained)
    """
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        sse_bus.subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/metrics")
async def metrics(user: User = Depends(require_user)):
    uptime = time.time() - _METRICS["boot_at"]
    from agent import metrics as metrics_mod
    snap = metrics_mod.snapshot()
    return {
        **_METRICS,
        "uptime_secs": round(uptime, 1),
        "counters": snap["counters"],
        "gauges": snap["gauges"],
    }


def build_diagnostics_report() -> dict:
    """Assemble a support-diagnostics bundle: versions, runtime shape,
    metrics, and the current config with provider secrets redacted (#488).

    Deliberately robust — this is the "something's broken, send me your
    setup" tool, so every sub-block is best-effort: a failure in one (e.g.
    metrics not yet initialised) records an ``error`` string for that block
    instead of blanking the whole report. Config runs through
    ``redact_secrets`` so no provider key ever leaves the box (same exposure
    as GET /api/config). The raw log is NOT inlined — it can carry
    transcripts — only its path is surfaced so the user attaches it via
    Settings → Diagnostics → Reveal logs.
    """
    import platform as _platform

    report: dict = {}

    try:
        from datetime import datetime, timezone
        report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        pass

    # --- app / interpreter versions -------------------------------------
    try:
        import importlib.metadata as _md
        app_version = _md.version("orbis")
    except Exception:
        app_version = "unknown"
    report["app"] = {
        "version": app_version,
        "python": _platform.python_version(),
        "platform": _platform.platform(),
        "machine": _platform.machine(),
    }

    # --- runtime shape (mirrors /healthz, no per-user state) -------------
    try:
        stt_config, tts_config = resolve_speech_config(get_active_persona())
        report["runtime"] = {
            "uptime_secs": round(time.time() - _METRICS["boot_at"], 1),
            "stt_backend": stt_config["backend"],
            "tts_backend": tts_config["backend"],
            "transport": "native",
            "persona": get_active_persona().slug,
            "active_sessions": len(active_user_states()),
            "socket_connected": bool(
                app._native_transport and app._native_transport.connected
            ),
            "pipeline_running": bool(
                app._voice_lifecycle.is_running()
                and app._native_transport
                and app._native_transport.connected
                and app._native_pipeline_task
                and not app._native_pipeline_task.done()
            ),
            "voice_lifecycle": app._voice_lifecycle.snapshot(),
            "half_duplex": HALF_DUPLEX,
        }
    except Exception as e:
        report["runtime"] = {"error": f"runtime probe failed: {e}"}

    # --- metrics counters -----------------------------------------------
    try:
        from agent import metrics as _metrics_mod
        snap = _metrics_mod.snapshot()
        report["metrics"] = {
            "sessions_total": _METRICS.get("sessions_total"),
            "tool_calls_total": _METRICS.get("tool_calls_total"),
            "a2a_inbound_total": _METRICS.get("a2a_inbound_total"),
            "counters": snap.get("counters", {}),
            "gauges": snap.get("gauges", {}),
        }
    except Exception as e:
        report["metrics"] = {"error": f"metrics unavailable: {e}"}

    # --- config, secrets redacted (same exposure as GET /api/config) -----
    try:
        from agent.config_store import read_config, redact_secrets
        report["config"] = redact_secrets(read_config())
    except Exception as e:
        report["config"] = {"error": f"config unavailable: {e}"}

    # --- where the raw log lives (NOT inlined; may carry transcripts) ----
    from pathlib import Path as _P
    log_path = _P.home() / "Library/Logs/studio.protolabs.orbis/sidecar.log"
    report["logs"] = {
        "path": str(log_path),
        "note": "Attach this file — Settings → Diagnostics → Reveal logs in Finder.",
    }

    return report


@router.get("/api/diagnostics")
async def diagnostics(user: User = Depends(require_user)):
    """Redacted support bundle for the in-app 'Copy diagnostics' button (#488).
    Auth'd like /api/config — it embeds the redacted config."""
    return build_diagnostics_report()


@router.get("/api/whoami")
async def whoami(user: User = Depends(require_user)):
    """Return the resolved owner. Clients call this at boot to confirm
    their API key is valid and get the display name for UI chrome."""
    return {
        "id": user.id,
        "display_name": user.display_name,
        "auth_source": user_registry.source,
    }


@router.get("/api/verbosity")
async def get_verbosity(user: User = Depends(require_user)):
    return {"verbosity": app.user_state_for(user.id).filler_settings.verbosity.value}


@router.post("/api/verbosity")
async def set_verbosity(body: dict, user: User = Depends(require_user)):
    from agent.filler import Verbosity
    state = app.user_state_for(user.id)
    try:
        state.filler_settings.verbosity = Verbosity(body.get("level", "").lower())
    except ValueError:
        return {"error": "level must be silent|brief|narrated|chatty"}
    return {"verbosity": state.filler_settings.verbosity.value}
