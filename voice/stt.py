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
  - `transcribe_bytes(audio_bytes)` one-shot transcribe for diagnostics
    and future import tools — also routes to the active backend.
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


def _detect_torch_device() -> str:
    """Pick the best available device for the HF Whisper pipeline.
    Mirrors ``agent.hardware.detect_device``'s preference order
    (cuda > mps > cpu) but never raises — STT also runs in tests / CI
    where CPU fallback is correct, while ``detect_device`` is the
    desktop-bundle gate that hard-fails when no accelerator is found.

    Honors the same ``ORBIS_ALLOW_CPU=1`` opt-in flag as
    ``agent.hardware.detect_device``: pytest and the Docker CPU profile
    set it to force CPU even when MPS / CUDA appear available
    (CI macOS MPS reports `is_available()` but has too small a memory
    budget for production-shaped workloads)."""
    if os.environ.get("ORBIS_ALLOW_CPU") == "1":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


DEVICE = _detect_torch_device()
# Half-precision on accelerators (CUDA + Apple MPS). Locally benchmarked
# Whisper-large-v3-turbo at fp16/MPS = ~2.6s for 2.6s audio (1.0x rtf)
# vs ~4.5s on CPU/fp32 (1.7x rtf) — clean 1.7x speedup with correct
# output. CPU stays float32 since fp16 on CPU is slower.
_WHISPER_DTYPE = torch.float16 if DEVICE in ("cuda", "mps") else torch.float32
# `sdpa` attention is CUDA-only in transformers; MPS uses the eager path.
_WHISPER_MODEL_KWARGS = (
    {"attn_implementation": "sdpa"} if DEVICE == "cuda" else {}
)

# OpenAI-compatible backend (also used by LocalAI / OpenRouter / etc.)
STT_URL = os.environ.get("STT_URL", "https://api.openai.com/v1")
STT_MODEL = os.environ.get("STT_MODEL", "whisper-1")
STT_API_KEY = os.environ.get("STT_API_KEY", "not-needed")


# ---------------------------------------------------------------------------
# Local Whisper — HF transformers pipeline
# ---------------------------------------------------------------------------

# Whisper hallucinates confidently on silence/noise. The gates below
# are tuned for the native CPAL path on M1 internal mic (no AGC) — but
# AFTER voice/local_transport.py applies the MIC_GAIN multiplier.
# Override via env vars for a different mic profile.
_STT_MIN_RMS = float(os.environ.get("STT_MIN_RMS", "0.025"))

# Short outputs (< _STT_MIN_TEXT_LEN chars) from low-energy chunks
# (RMS < _STT_STRONG_RMS) are dropped. Real utterances clear at least
# one of these — a long sentence at any volume, or a short word at
# clear-speech volume.
_STT_MIN_TEXT_LEN = int(os.environ.get("STT_MIN_TEXT_LEN", "10"))
_STT_STRONG_RMS = float(os.environ.get("STT_STRONG_RMS", "0.15"))

# Common Whisper artifacts when the input is silence / breath / room tone.
# Match after lowercase + punctuation/whitespace normalization. Keep this
# list conservative — false positives steal real user input.
_HALLUCINATION_PHRASES = frozenset(
    s.strip().lower().rstrip(".!?,") for s in [
        "thanks for watching",
        "thank you for watching",
        "thanks for watching!",
        "thanks for watching.",
        "thank you for watching.",
        "thanks for listening",
        "thank you for listening",
        "thank you",
        "thanks",
        "you",
        "yeah",
        "bye",
        "okay",
        ".com",
        "[music]",
        "[silence]",
        "♪",
    ]
)


def _is_whisper_hallucination(text: str) -> bool:
    if not text:
        return False
    norm = text.strip().lower().rstrip(".!?,")
    return norm in _HALLUCINATION_PHRASES


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
        # Use the canonical `dtype` arg, not the deprecated `torch_dtype`.
        # transformers >=4.46 routes `torch_dtype` through an internal
        # dtype path that conflicts with model_kwargs ("use only one"),
        # which surfaced as intermittent "STT inference failed" errors.
        dtype=_WHISPER_DTYPE,
        device=DEVICE,
        model_kwargs=_WHISPER_MODEL_KWARGS,
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
        # `audio` arrives as a WAV-formatted byte buffer aggregated by
        # pipecat's SegmentedSTTService between VAD start/end markers.
        # Logging the raw size makes it obvious when an empty/tiny
        # segment slipped through (which Whisper transcribes to "" or
        # garbage and the pipeline silently no-ops downstream).
        logger.info(f"[stt.local] run_stt received audio bytes={len(audio)}")
        try:
            data, sr = sf.read(io.BytesIO(audio), dtype="float32")
        except Exception as e:
            logger.warning(f"[stt.local] decode failed: {e}")
            yield ErrorFrame(error=f"STT decode failed: {e}")
            return

        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != 16000:
            data = soxr.resample(data, sr, 16000)

        duration_s = len(data) / 16000.0
        rms = float(np.sqrt(np.mean(data * data))) if data.size else 0.0
        logger.info(
            f"[stt.local] decoded sr_in={sr} samples={data.size} "
            f"duration={duration_s:.2f}s rms={rms:.4f}"
        )
        # Whisper hallucinates confidently on near-silent audio
        # ("Thanks for watching", "you", ".com", etc. — artifacts from
        # YouTube training data). VAD with a relaxed min_volume can let
        # quiet chunks through; this is the second gate. The threshold is
        # below typical speech (RMS ~0.03–0.10 on the M1 internal mic
        # without AGC) but above ambient/breath (~0.005–0.015).
        if rms < _STT_MIN_RMS:
            logger.info(f"[stt.local] skipped — rms {rms:.4f} below threshold {_STT_MIN_RMS}")
            return
        with tracing.span(
            "stt.whisper",
            input={"sample_rate": 16000, "audio_seconds": round(duration_s, 2)},
            metadata={"backend": "local"},
        ) as sp:
            try:
                t0 = time.time()
                result = _get_local_pipe()({"raw": data.flatten(), "sampling_rate": 16000})
                text = (result.get("text") or "").strip()
                if _is_whisper_hallucination(text):
                    logger.info(f"[stt.local] dropped hallucination: {text!r}")
                    text = ""
                # Short outputs from sub-speech-energy chunks (sniffs,
                # clicks, keyboard taps) tend to be junk that wasn't in
                # our static blocklist. Require either a normal RMS OR
                # enough characters to look like a real utterance.
                elif text and len(text) < _STT_MIN_TEXT_LEN and rms < _STT_STRONG_RMS:
                    logger.info(
                        f"[stt.local] dropped short low-rms output: {text!r} "
                        f"(rms={rms:.4f}, len={len(text)})"
                    )
                    text = ""
                elif text:
                    # Log the full transcript at INFO when something
                    # passes the gates — useful for tuning the filters
                    # without flipping the whole logger to DEBUG.
                    logger.info(f"[stt.local] transcript: {text!r}")
                # INFO log carries duration + length only — every voice
                # utterance flows through here, so the full transcript
                # at INFO would write the user's spoken content to
                # whatever log sink the deployment uses (file, journald,
                # remote shipper). The text is still observable at DEBUG
                # for local debugging.
                logger.info(
                    f"[stt.local] whisper {duration_s:.2f}s → "
                    f"{(time.time() - t0):.2f}s → {len(text)} chars"
                )
                if text:
                    logger.debug(f"[stt.local] text={text!r}")
            except Exception as e:
                sp.update(level="ERROR", status_message=str(e))
                logger.error(f"[stt.local] inference failed: {e}")
                yield ErrorFrame(error=f"STT inference failed: {e}")
                return
            sp.update(output=text)

        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601())
        else:
            logger.info("[stt.local] empty transcription — no frame emitted")


# ---------------------------------------------------------------------------
# Public factory + helpers
# ---------------------------------------------------------------------------

def make_stt(
    *,
    backend: str | None = None,
    whisper_model: str | None = None,
    url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    **_unused,
) -> SegmentedSTTService:
    """Return the configured STT service for the pipeline."""
    chosen_backend = (backend or STT_BACKEND or "local").strip().lower()
    if chosen_backend == "openai":
        chosen_url = url or STT_URL
        chosen_model = model or STT_MODEL
        chosen_api_key = api_key or STT_API_KEY
        logger.info(f"STT backend: openai @ {chosen_url} model={chosen_model}")
        return OpenAISTTService(
            api_key=chosen_api_key,
            base_url=chosen_url,
            settings=STTSettings(model=chosen_model, language=None),
        )
    if chosen_backend == "sensevoice":
        # Lazy import keeps funasr in the [sensevoice] optional extra —
        # local + openai backends still work without it installed.
        from voice.stt_sensevoice import SenseVoiceSTT
        logger.info("STT backend: sensevoice (FunAudioLLM/SenseVoiceSmall)")
        return SenseVoiceSTT()
    if chosen_backend == "parakeet":
        # Lazy import keeps parakeet-mlx in the [parakeet] optional extra.
        # NVIDIA Parakeet-TDT on MLX — faster + far fewer silence
        # hallucinations than Whisper.
        from voice.stt_parakeet import ParakeetMLXSTT
        logger.info("STT backend: parakeet (NVIDIA Parakeet-TDT via MLX)")
        return ParakeetMLXSTT()
    if chosen_backend != "local":
        logger.warning(f"Unknown STT backend={chosen_backend!r}; falling back to local")
    if whisper_model and whisper_model != WHISPER_MODEL:
        logger.warning(
            "WHISPER_MODEL override %r differs from process model %r; "
            "restart with WHISPER_MODEL set to change local Whisper weights",
            whisper_model,
            WHISPER_MODEL,
        )
    return LocalWhisperSTT()


def prewarm() -> None:
    if STT_BACKEND == "openai":
        # No model-load step on a remote endpoint; pipecat's first call
        # opens the connection. Skip explicit warm.
        logger.info("STT backend: openai (no local prewarm)")
        return
    if STT_BACKEND == "sensevoice":
        from voice.stt_sensevoice import prewarm as prewarm_sensevoice
        prewarm_sensevoice()
        return
    if STT_BACKEND == "parakeet":
        from voice.stt_parakeet import prewarm as prewarm_parakeet
        prewarm_parakeet()
        return
    _get_local_pipe()


# ---------------------------------------------------------------------------
# Backend-aware one-shot transcribe.
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

    Keep a sync interface so FastAPI callers can use `asyncio.to_thread`.
    For OpenAI backend we use httpx-sync to avoid nesting event loops.
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
