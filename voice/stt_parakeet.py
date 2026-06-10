"""Parakeet STT backend — NVIDIA Parakeet-TDT via Apple MLX.

Set ``STT_BACKEND=parakeet`` to transcribe with NVIDIA's Parakeet-TDT on
Apple Silicon (through ``parakeet-mlx``) instead of Whisper. It's faster,
more accurate, and far less prone to the silence-hallucinations Whisper
produces ("Thanks for watching", "I'll see you next time", etc.) — the
reason ORBIS kept "replying" to silence.

Lives behind the ``[parakeet]`` optional extra so the mlx / parakeet deps
stay opt-in; ``STT_BACKEND=local`` (Whisper) and ``=openai`` work without
it installed.

Model: ``mlx-community/parakeet-tdt-0.6b-v3`` (~600 MB, downloaded to the
HF cache on first use). We decode the WAV segment ourselves and feed the
float32 PCM straight into ``get_logmel`` + ``generate`` — bypassing
parakeet-mlx's ffmpeg-based ``load_audio`` (ffmpeg isn't bundled in the
sidecar).

Threading: MLX is thread-sensitive — using a model loaded on one thread
from another raises "There is no Stream(gpu, 0) in current thread". So
ALL MLX work (load + every inference) is dispatched to a single dedicated
worker thread. That also keeps the GIL-heavy inference off the asyncio
event loop.

References:
- parakeet-mlx: https://github.com/senstella/parakeet-mlx
- Mirrors the ``LocalWhisperSTT`` / ``SenseVoiceSTT`` contract.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import io
import logging
import os
import time
from collections.abc import AsyncGenerator

import numpy as np
import soundfile as sf
import soxr
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601

logger = logging.getLogger(__name__)

_MODEL_ID = os.environ.get("PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v3")
# Silence gate on RMS ([-1, 1] PCM). Parakeet hallucinates far less than
# Whisper, but skipping near-silent segments still saves compute.
_MIN_RMS = float(os.environ.get("PARAKEET_MIN_RMS", "0.008"))

# Single dedicated thread for ALL MLX work (load + inference) so they
# share the same MLX stream and never block the event loop.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="parakeet-mlx"
)
_model = None


def _load_model_sync():
    """Load (once) on the dedicated MLX thread."""
    global _model
    if _model is not None:
        return _model
    from parakeet_mlx import from_pretrained

    logger.info(f"Loading Parakeet {_MODEL_ID} (MLX)")
    t0 = time.time()
    model = from_pretrained(_MODEL_ID)
    sr = model.preprocessor_config.sample_rate
    try:
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel

        mel = get_logmel(mx.zeros(sr, dtype=mx.float32), model.preprocessor_config)
        model.generate(mel)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[stt.parakeet] warmup skipped: {e}")
    _model = model
    logger.info(f"Parakeet ready in {time.time() - t0:.1f}s (sr={sr})")
    return _model


def _transcribe_sync(data: np.ndarray, sr: int) -> str:
    """Resample + transcribe on the dedicated MLX thread."""
    import mlx.core as mx
    from parakeet_mlx.audio import get_logmel

    model = _load_model_sync()
    target_sr = model.preprocessor_config.sample_rate
    if sr != target_sr:
        data = soxr.resample(data, sr, target_sr)
    # Mirror parakeet-mlx's transcribe() no-chunk path, but feed our own
    # decoded float32 array (no load_audio()/ffmpeg).
    mel = get_logmel(mx.array(data.astype(np.float32)), model.preprocessor_config)
    results = model.generate(mel)
    return (results[0].text if results else "").strip()


def prewarm() -> None:
    # Load on the dedicated thread so inference (also dispatched there)
    # shares the same MLX stream.
    _executor.submit(_load_model_sync).result()


class ParakeetMLXSTT(SegmentedSTTService):
    """Pipecat STT wrapper around parakeet-mlx (Parakeet-TDT on MLX)."""

    def __init__(self, *, user_id: str = "user", **kwargs):
        kwargs.setdefault("settings", STTSettings(model=None, language=None))
        super().__init__(**kwargs)
        self._user_id = user_id

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        logger.info(f"[stt.parakeet] run_stt received audio bytes={len(audio)}")
        try:
            data, sr = sf.read(io.BytesIO(audio), dtype="float32")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[stt.parakeet] decode failed: {e}")
            yield ErrorFrame(error=f"STT decode failed: {e}")
            return

        if data.ndim > 1:
            data = data.mean(axis=1)

        rms = float(np.sqrt(np.mean(data * data))) if data.size else 0.0
        if rms < _MIN_RMS:
            logger.info(f"[stt.parakeet] skipped — rms {rms:.4f} below {_MIN_RMS}")
            return

        try:
            t0 = time.time()
            loop = asyncio.get_event_loop()
            # Inference on the dedicated MLX thread (consistent stream +
            # off the event loop).
            text = await loop.run_in_executor(_executor, _transcribe_sync, data, sr)
            # Latency + length at INFO; the transcript itself (the user's
            # private spoken content) only at DEBUG so it doesn't persist in
            # the shareable sidecar.log. See audit L1.
            logger.info(f"[stt.parakeet] {time.time() - t0:.2f}s → {len(text)} chars")
            logger.debug(f"[stt.parakeet] transcript: {text!r}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[stt.parakeet] inference failed: {e}")
            yield ErrorFrame(error=f"STT inference failed: {e}")
            return

        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601())
        else:
            logger.info("[stt.parakeet] empty transcription — no frame emitted")
