"""STT backends — `local` (HuggingFace Whisper) or `openai` (any
OpenAI-compatible /v1/audio/transcriptions endpoint).

Selected via `STT_BACKEND={local|openai}`. Default `local`.

Local backend keeps Whisper large-v3-turbo on the GPU. OpenAI backend
points at OpenAI itself, LocalAI, OpenRouter, vLLM-omni, or anything
that exposes the same wire format — useful for hosts without a GPU or
when you want STT to live on the same box as your LLM gateway.

Both backends expose the same module-level helpers:
  - `make_stt()` returns a Pipecat STTService
  - `prewarm()` warms whichever backend is selected
  - `transcribe_bytes(audio_bytes)` one-shot transcribe (used by the
    voice-clone endpoint) — also routes to the active backend.
"""

from __future__ import annotations

import io
import logging
import os
import time
from collections.abc import AsyncGenerator

import httpx
import numpy as np
import soundfile as sf
import soxr

from agent import tracing
import torch
from openai import AsyncOpenAI
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601
from transformers import pipeline as hf_pipeline

logger = logging.getLogger(__name__)

STT_BACKEND = os.environ.get("STT_BACKEND", "local").lower()

# Local backend
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "openai/whisper-large-v3-turbo")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# OpenAI-compatible backend (also used by LocalAI / OpenRouter / etc.)
STT_URL = os.environ.get("STT_URL", "https://api.openai.com/v1")
STT_MODEL = os.environ.get("STT_MODEL", "whisper-1")
STT_API_KEY = os.environ.get("STT_API_KEY", "not-needed")


# ---------------------------------------------------------------------------
# Local Whisper — HF transformers pipeline
# ---------------------------------------------------------------------------

_local_pipe = None


def _get_local_pipe():
    global _local_pipe
    if _local_pipe is not None:
        return _local_pipe
    logger.info(f"Loading {WHISPER_MODEL} on {DEVICE}")
    t0 = time.time()
    _local_pipe = hf_pipeline(
        "automatic-speech-recognition",
        model=WHISPER_MODEL,
        torch_dtype=torch.float16 if DEVICE == "cuda" else torch.float32,
        device=DEVICE,
        model_kwargs={"attn_implementation": "sdpa"} if DEVICE == "cuda" else {},
    )
    _local_pipe({"raw": np.zeros(16000, dtype=np.float32), "sampling_rate": 16000})
    logger.info(f"Whisper ready in {time.time() - t0:.1f}s")
    return _local_pipe


class LocalWhisperSTT(SegmentedSTTService):
    """Pipecat STT wrapper around an in-process HuggingFace Whisper pipeline."""

    def __init__(self, *, user_id: str = "user", **kwargs):
        kwargs.setdefault("settings", STTSettings(model=None, language=None))
        super().__init__(**kwargs)
        self._user_id = user_id

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        try:
            data, sr = sf.read(io.BytesIO(audio), dtype="float32")
        except Exception as e:
            yield ErrorFrame(error=f"STT decode failed: {e}")
            return

        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != 16000:
            data = soxr.resample(data, sr, 16000)

        duration_s = len(data) / 16000.0
        with tracing.span(
            "stt.whisper",
            input={"sample_rate": 16000, "audio_seconds": round(duration_s, 2)},
            metadata={"backend": "local"},
        ) as sp:
            try:
                result = _get_local_pipe()({"raw": data.flatten(), "sampling_rate": 16000})
                text = (result.get("text") or "").strip()
            except Exception as e:
                sp.update(level="ERROR", status_message=str(e))
                yield ErrorFrame(error=f"STT inference failed: {e}")
                return
            sp.update(output=text)

        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601())


# ---------------------------------------------------------------------------
# Public factory + helpers
# ---------------------------------------------------------------------------

def make_stt() -> SegmentedSTTService:
    """Return the configured STT service for the pipeline."""
    if STT_BACKEND == "openai":
        logger.info(f"STT backend: openai @ {STT_URL} model={STT_MODEL}")
        return OpenAISTTService(
            api_key=STT_API_KEY,
            base_url=STT_URL,
            settings=STTSettings(model=STT_MODEL, language=None),
        )
    if STT_BACKEND != "local":
        logger.warning(f"Unknown STT_BACKEND={STT_BACKEND!r}; falling back to local")
    return LocalWhisperSTT()


def prewarm() -> None:
    if STT_BACKEND == "openai":
        # No model-load step on a remote endpoint; pipecat's first call
        # opens the connection. Skip explicit warm.
        logger.info(f"STT backend: openai (no local prewarm)")
        return
    _get_local_pipe()


# ---------------------------------------------------------------------------
# Backend-aware one-shot transcribe — used by /api/voice/clone
# ---------------------------------------------------------------------------

def _transcribe_local(audio_bytes: bytes) -> str:
    data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        data = soxr.resample(data, sr, 16000)
    result = _get_local_pipe()({"raw": data.flatten(), "sampling_rate": 16000})
    return (result.get("text") or "").strip()


_openai_client: AsyncOpenAI | None = None


def _openai_async_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=STT_API_KEY, base_url=STT_URL)
    return _openai_client


async def _transcribe_openai_async(audio_bytes: bytes, *, filename: str = "audio.wav") -> str:
    """Hit /v1/audio/transcriptions via the OpenAI client. Works against
    OpenAI proper, LocalAI, and other compat endpoints."""
    f = io.BytesIO(audio_bytes)
    f.name = filename  # SDK uses the name as the upload filename
    r = await _openai_async_client().audio.transcriptions.create(
        model=STT_MODEL,
        file=f,
        response_format="text",
    )
    # `text` response_format returns a plain string; fall back to .text attr.
    return (r if isinstance(r, str) else getattr(r, "text", "") or "").strip()


def transcribe_bytes(audio_bytes: bytes) -> str:
    """Synchronous one-shot transcribe routed by STT_BACKEND.

    The voice-clone endpoint calls this from an `asyncio.to_thread` so a
    sync interface is the cleanest. For OpenAI backend we run the async
    client via httpx-sync to avoid nesting event loops.
    """
    if STT_BACKEND == "openai":
        return _transcribe_openai_sync(audio_bytes)
    return _transcribe_local(audio_bytes)


def _transcribe_openai_sync(audio_bytes: bytes, *, filename: str = "audio.wav") -> str:
    """Pure-sync version using httpx — avoids `asyncio.run` nesting when
    the caller is already inside an event loop (FastAPI handler running
    via `to_thread`)."""
    headers = {}
    if STT_API_KEY and STT_API_KEY != "not-needed":
        headers["Authorization"] = f"Bearer {STT_API_KEY}"
    files = {"file": (filename, audio_bytes, "audio/wav")}
    data = {"model": STT_MODEL, "response_format": "text"}
    r = httpx.post(
        f"{STT_URL.rstrip('/')}/audio/transcriptions",
        headers=headers,
        files=files,
        data=data,
        timeout=60.0,
    )
    r.raise_for_status()
    return r.text.strip()
