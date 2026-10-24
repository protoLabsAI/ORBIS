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
from app import _reconfigure_live_llm, _resolve_skill_llm, logger


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
    from agent.config_store import (
        applies_map,
        merge_patch_with_warnings,
        read_config,
        redact_secrets,
    )

    previous = read_config()
    try:
        normalized, warnings = merge_patch_with_warnings(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    persona = reload_persona()
    applies = applies_map(previous, normalized)
    # Hot-swap the live LLM (no restart) when the llm block changed — mirrors
    # the runtime TTS-voice swap. The swap is refused (and says why) whenever
    # it could not truthfully take effect; see _reconfigure_live_llm. It is
    # given the RESOLVED config so api_key_env / env fallbacks / extra_body
    # derive exactly as at pipeline build.
    llm_apply: dict | None = None
    if isinstance(patch, dict) and "llm" in patch:
        try:
            llm_apply = _reconfigure_live_llm(
                _resolve_skill_llm(persona), user_id="default"
            )
        except Exception as exc:  # noqa: BLE001 — surface, never 500 a saved config
            logger.warning(f"[llm] live reconfigure raised: {exc}")
            llm_apply = {"applied": "failed", "reason": str(exc)}
        applies["llm"] = llm_apply["applied"]
    return {
        "ok": True,
        # Redacted exactly like GET — never echo stored provider keys.
        "config": redact_secrets(normalized),
        "persona": persona.slug,
        # Back-compat boolean; `llm_apply` carries the real outcome + reason.
        "llm_applied_live": bool(llm_apply and llm_apply["applied"] == "live"),
        "llm_apply": (
            {"applied": llm_apply["applied"], "reason": llm_apply["reason"]}
            if llm_apply
            else None
        ),
        "applies": applies,
        "warnings": warnings,
    }
