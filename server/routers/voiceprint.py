"""Speaker-verification enrollment routes — extracted from app.py (#app.py-decomposition).

Library names import from their origin module; app-defined names via
`from app import`; the monkeypatched/mutable set as `app.<name>` at call
time (so live values + test monkeypatches win).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from agent.paths import get_voiceprint_path
from agent.speaker_gate import save_voiceprint
from auth import require_user
from auth.users import User
from app import logger


router = APIRouter()


_VOICEPRINT_MIN_DURATION_SECS = 3.0
_VOICEPRINT_MAX_DURATION_SECS = 30.0


@router.get("/api/voiceprint/status")
async def voiceprint_status(_user: User = Depends(require_user)):
    """Tells the wizard whether enrollment is needed."""
    p = get_voiceprint_path()
    return {
        "enrolled": p.exists(),
        "path": str(p),
        # speechbrain may not be installed even when a voiceprint exists,
        # so the wizard can show a different "install [speaker-id]"
        # message. Probed cheaply.
        "embedder_available": _is_speechbrain_available(),
    }


def _is_speechbrain_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("speechbrain") is not None


@router.post("/api/voiceprint/enroll")
async def voiceprint_enroll(
    request: Request,
    user: User = Depends(require_user),
):
    """Encode + save an enrollment recording. Body is raw WAV bytes.

    Returns 200 with metadata on success, 4xx on validation, 501 if
    speechbrain isn't installed (bundled deployments can opt-in via
    ``pip install -e ".[speaker-id]"``)."""
    if not _is_speechbrain_available():
        raise HTTPException(
            status_code=501,
            detail=(
                "speechbrain not installed; cannot enroll. Install via "
                "`pip install -e \".[speaker-id]\"` or set "
                "`persona.behavior.speaker_gate: false` to keep owner-trust mode."
            ),
        )

    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="empty audio body")

    import io
    import soundfile as sf
    try:
        wav, sample_rate = sf.read(io.BytesIO(audio), dtype="float32")
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"could not decode audio: {e}; expected WAV bytes",
        )

    # Downmix to mono if needed.
    import numpy as _np
    if wav.ndim == 2:
        wav = wav.mean(axis=1)

    duration = len(wav) / float(sample_rate) if sample_rate else 0.0
    if duration < _VOICEPRINT_MIN_DURATION_SECS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"recording is {duration:.1f}s; need at least "
                f"{_VOICEPRINT_MIN_DURATION_SECS}s of audio for a stable embedding"
            ),
        )
    if duration > _VOICEPRINT_MAX_DURATION_SECS:
        # Truncate rather than reject — better UX than 400-ing on 31s.
        wav = wav[: int(_VOICEPRINT_MAX_DURATION_SECS * sample_rate)]
        duration = _VOICEPRINT_MAX_DURATION_SECS

    try:
        from agent.ecapa_embedder import ECAPAEmbedder
        embedder = ECAPAEmbedder()
        emb = embedder.encode(wav.astype(_np.float32, copy=False), sample_rate=sample_rate)
    except ImportError as e:
        # Should be unreachable given the precheck, but defend in depth.
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.exception("[enroll] embedding failed")
        raise HTTPException(
            status_code=500,
            detail=f"failed to encode embedding: {e}",
        )

    target = get_voiceprint_path()
    try:
        save_voiceprint(target, emb)
    except Exception as e:
        logger.exception("[enroll] save failed")
        raise HTTPException(status_code=500, detail=f"could not save voiceprint: {e}")

    logger.info(
        f"[enroll] saved voiceprint for user={user.id!r} "
        f"({duration:.1f}s @ {sample_rate} Hz, dim={emb.shape[0]}, path={target})"
    )
    return {
        "enrolled": True,
        "path": str(target),
        "duration_secs": round(duration, 2),
        "sample_rate": int(sample_rate),
        "embedding_dim": int(emb.shape[0]),
    }


@router.delete("/api/voiceprint")
async def voiceprint_delete(user: User = Depends(require_user)):
    """Remove the cached voiceprint — gate falls back to owner-trust on
    the next session. Use to start a fresh enrollment from the drawer
    UI or via curl when the audio sample needs to be replaced."""
    p = get_voiceprint_path()
    if p.exists():
        try:
            p.unlink()
            logger.info(f"[enroll] deleted voiceprint at {p} for user={user.id!r}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"deleted": True, "path": str(p)}
