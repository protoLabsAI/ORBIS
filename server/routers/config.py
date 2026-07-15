"""Config read/write routes — extracted from app.py (#app.py-decomposition).

Library names import from their origin module; app-defined names via
`from app import`; the monkeypatched/mutable set as `app.<name>` at call
time (so live values + test monkeypatches win).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agent.persona import reload_persona
from auth import require_user
from auth.users import User
from voice.stt import STT_BACKEND
from app import _reconfigure_live_llm


router = APIRouter()


@router.get("/api/config")
async def get_config(user: User = Depends(require_user)):
    """Return the current config/orbis.yaml as a dict. Drawer UI
    consumes this to populate the settings form."""
    from agent.config_store import read_config, redact_secrets
    cfg = read_config()
    # Surface the *effective* STT backend so Settings reflects what's
    # actually running. The native build defaults the backend via the
    # STT_BACKEND env (parakeet); if the user hasn't explicitly set
    # stt.backend in config, fill it in so the UI doesn't show a stale
    # default ("Whisper") while Parakeet is the live backend.
    stt = cfg.get("stt")
    if not isinstance(stt, dict):
        stt = {}
    if not stt.get("backend"):
        stt["backend"] = STT_BACKEND
        cfg["stt"] = stt
    # Never return stored provider secrets — the UI only needs to know a
    # key is set (it shows a "saved" indicator). Redaction also closes the
    # cleartext-key exposure if this route is ever reached unauthenticated
    # (single-user fallback + a non-loopback bind). See audit H3/M11.
    return {"config": redact_secrets(cfg)}


@router.post("/api/config")
async def put_config(patch: dict, user: User = Depends(require_user)):
    """Apply a shallow-merge patch to config/orbis.yaml. Returns the
    normalized post-write config. Reloads the in-memory persona so
    the next voice session uses the new values.

    Body shape is a partial config::

        {"persona": {"name": "Atlas"}}              # rename
        {"voice": {"tts_backend": "elevenlabs"}}    # swap provider
        {"orb": {"variant": "nebula", "palette": "Helios"}}   # restyle

    All blocks are freely editable — ORBIS is free + open source, so orb
    customization is ungated. Starter-orb selection happens via
    /api/orb/select_starter (restricted to the curated pool).

    Drops unknown keys with a warning. Raises 400 on typed failures
    (invalid tts_backend, non-numeric temperature, etc.).
    """
    from agent.config_store import merge_patch

    try:
        normalized = merge_patch(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    persona = reload_persona()
    # Hot-swap the live LLM (no restart) when the llm block changed — mirrors
    # the runtime TTS-voice swap. MLX (in-process) can't retarget live, so
    # `llm_applied_live` stays False there and the UI keeps the restart hint.
    applied_live = False
    if isinstance(patch, dict) and "llm" in patch:
        new_llm = normalized.get("llm") if isinstance(normalized, dict) else None
        res = _reconfigure_live_llm(new_llm or {}, user_id="default")
        applied_live = bool(res.get("ok"))
    return {
        "ok": True,
        "config": normalized,
        "persona": persona.slug,
        "llm_applied_live": applied_live,
    }
