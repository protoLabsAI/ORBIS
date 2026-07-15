"""Voice model download: TTS voices, wakeword, STT/LLM assets — extracted from app.py (#app.py-decomposition).

Library names import from their origin module; app-defined names via
`from app import`; the monkeypatched/mutable set as `app.<name>` at call
time (so live values + test monkeypatches win).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from auth import require_user
from auth.users import User
from fastapi.responses import JSONResponse
from voice.sse_bus import sse_bus
from voice.stt import STT_BACKEND, prewarm as prewarm_stt
from voice.tts import TTS_BACKEND, prewarm as prewarm_tts
from app import _switch_live_voice


router = APIRouter()


@router.post("/api/voice/download_models")
async def voice_download_models():
    """Download + warm the on-device speech models (Parakeet STT + Kokoro TTS),
    streaming byte progress as SSE.

    The setup wizard's "Install on-device models" choice calls this so the
    ~900 MB pull happens *then*, with a progress bar — instead of being silently
    deferred to the next launch (prewarm_all only downloads on-device models when
    ``voice.local_models == "on_device"`` is already set at boot) and stalling
    the first voice turn with no feedback. Idempotent: re-running with weights
    cached + loaded returns fast (the prewarm fns short-circuit on a warm model).

    Emits ``data: {status, completed, total}`` byte-progress events, then a final
    ``event: done`` (or ``event: error``). Unauth — same rationale as
    ``/api/llm/mlx/pull``: it only pulls public model weights from HuggingFace.
    """
    from fastapi.responses import StreamingResponse
    import asyncio as _asyncio
    from pathlib import Path as _Path

    # Only local backends pull weights. Map the configured STT/TTS backend to its
    # HuggingFace repo (the progress denominator) + its prewarm fn (downloads,
    # then warms on the right thread). Cloud backends carry no big local download.
    targets: list = []  # (label, repo_id, prewarm_fn)
    if STT_BACKEND == "parakeet":
        from voice.stt_parakeet import _MODEL_ID as _stt_repo
        targets.append(("speech recognition", _stt_repo, prewarm_stt))
    elif STT_BACKEND == "local":
        from voice.stt import WHISPER_MODEL as _stt_repo
        targets.append(("speech recognition", _stt_repo, prewarm_stt))
    if TTS_BACKEND == "kokoro":
        targets.append(("voice", "hexgrad/Kokoro-82M", prewarm_tts))

    async def _stream():
        try:
            from huggingface_hub import HfApi
        except ImportError as e:
            yield f"event: error\ndata: huggingface_hub not available: {e}\n\n"
            return

        if not targets:
            # BYO / cloud backends — nothing to download.
            yield 'data: {"status": "no on-device models needed", "completed": 0, "total": 0}\n\n'
            yield "event: done\ndata: {}\n\n"
            return

        loop = _asyncio.get_running_loop()
        hf_home = os.environ.get("HF_HOME", str(_Path.home() / ".cache/huggingface"))
        hub = _Path(hf_home) / "hub"

        def _cache_dir(repo_id: str) -> _Path:
            return hub / f"models--{repo_id.replace('/', '--')}"

        def _dir_size(p: _Path) -> int:
            if not p.exists():
                return 0
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

        # One combined progress bar across every target repo.
        yield 'data: {"status": "fetching model details", "completed": 0, "total": 0}\n\n'
        total_bytes = 0
        for _label, repo_id, _fn in targets:
            try:
                info = await loop.run_in_executor(
                    None, lambda rid=repo_id: HfApi().repo_info(rid, files_metadata=True)
                )
                total_bytes += sum(f.size or 0 for f in (info.siblings or []))
            except Exception:  # noqa: BLE001
                pass  # percent may be missing; byte counts still stream

        done_bytes = 0  # bytes credited from already-finished targets
        for label, repo_id, fn in targets:
            cache_dir = _cache_dir(repo_id)
            base = done_bytes
            yield (
                f'data: {{"status": "downloading {label}", '
                f'"completed": {base + _dir_size(cache_dir)}, "total": {total_bytes}}}\n\n'
            )
            fut = loop.run_in_executor(None, fn)
            last = -1
            while not fut.done():
                completed = base + _dir_size(cache_dir)
                if completed != last:
                    yield (
                        f'data: {{"status": "downloading {label}", '
                        f'"completed": {completed}, "total": {total_bytes}}}\n\n'
                    )
                    last = completed
                await _asyncio.sleep(0.4)
            try:
                await fut
            except Exception as e:  # noqa: BLE001
                yield f"event: error\ndata: {label}: {str(e)[:200]}\n\n"
                return
            done_bytes = base + _dir_size(cache_dir)

        yield (
            f'data: {{"status": "done", "completed": {done_bytes}, '
            f'"total": {total_bytes or done_bytes}}}\n\n'
        )
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/wakeword/models")
async def wakeword_models_list(user: User = Depends(require_user)):
    """Wake-word model catalog with live download status — drives the picker."""
    from voice.wakeword_models import catalog
    return {"models": catalog()}


@router.post("/api/wakeword/models/{model_id}/download")
async def wakeword_model_download(model_id: str, user: User = Depends(require_user)):
    """Download a wake-word model, awaited through the Rust ``api_request``
    proxy (models are small — 0.2–1.7 MB — so a blocking POST is fine).

    Progress is pushed to the ``/api/events`` SSE bus as ``wakeword-download``
    ``{id, downloaded, total, pct}`` — the Rust ``bridge_sse`` re-emits the bus
    as ``orbis-sse`` Tauri events, which is the reliable server→client channel
    in the bundled Tahoe WKWebView (a streamed POST body is not). A final
    ``{id, done: true}`` (or ``{id, error}``) marks completion."""
    import time as _time

    from voice.wakeword_models import get, iter_download

    if get(model_id) is None:
        return JSONResponse(
            status_code=404, content={"error": f"unknown model {model_id!r}"}
        )
    last = 0.0
    try:
        async for p in iter_download(model_id):
            now = _time.monotonic()
            if now - last < 0.1 and p["downloaded"] < p["total"]:
                continue  # throttle to ~10/s, but always emit the final tick
            last = now
            pct = (p["downloaded"] / p["total"] * 100) if p["total"] else 0
            sse_bus.publish_sync(
                "wakeword-download", {"id": model_id, "pct": pct, **p}
            )
    except Exception as e:  # noqa: BLE001
        sse_bus.publish_sync("wakeword-download", {"id": model_id, "error": str(e)})
        return JSONResponse(status_code=502, content={"error": str(e)})
    sse_bus.publish_sync("wakeword-download", {"id": model_id, "done": True})
    return {"ok": True}


@router.delete("/api/wakeword/models/{model_id}")
async def wakeword_model_delete(model_id: str, user: User = Depends(require_user)):
    """Remove a downloaded wake-word model."""
    from voice.wakeword_models import delete

    return {"ok": delete(model_id)}


@router.get("/api/tts/voices")
async def tts_voices(
    backend: str = "kokoro",
    user: User = Depends(require_user),
):
    """Enumerate voices/references for a TTS backend.

    The native settings panel uses this to render a picker where the
    backend has a finite catalogue. Each entry is ``{id, label, ...}``;
    backends that cache voices on disk include ``cached``.
    """
    backend = (backend or "kokoro").strip().lower()
    if backend == "kokoro":
        from voice.tts.kokoro import list_voices
        return {"backend": "kokoro", "voices": list_voices()}
    if backend == "openai":
        from voice.tts.openai import OPENAI_TTS_VOICES
        return {
            "backend": "openai",
            "voices": [{"id": v, "label": v} for v in OPENAI_TTS_VOICES],
        }
    if backend == "fish":
        from voice.tts.fish import FISH_URL, list_references
        refs = list_references()
        return {
            "backend": "fish",
            "fish_url": FISH_URL,
            "voices": [{"id": r, "label": r} for r in refs],
        }
    if backend == "elevenlabs":
        return {"backend": "elevenlabs", "voices": []}
    return {"backend": backend, "voices": [], "error": f"unknown backend: {backend!r}"}


@router.post("/api/tts/voices/download")
async def tts_download_voice(
    payload: dict,
    user: User = Depends(require_user),
):
    """Eagerly download a Kokoro voice tensor into the local HF cache."""
    backend = (payload.get("backend") or "").strip().lower()
    voice = (payload.get("voice") or "").strip()
    if not voice:
        return {"ok": False, "error": "voice is required"}
    if backend == "kokoro":
        from voice.tts.kokoro import download_voice
        return download_voice(voice)
    return {"ok": False, "error": f"{backend!r} backend has no downloadable voices"}


@router.post("/api/tts/voice")
async def tts_set_voice(payload: dict, user: User = Depends(require_user)):
    """Switch the live TTS voice mid-session (no rebuild/reconnect). The next
    spoken line uses it. Body: ``{voice: "af_bella"}``."""
    return _switch_live_voice(payload.get("voice") or "")
