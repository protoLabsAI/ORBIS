"""SenseVoice-Small STT backend (#66 Phase 2).

Replaces ``LocalWhisperSTT`` when ``STT_BACKEND=sensevoice`` — one model
that does STT + emotion + audio events in a single forward pass instead
of running Whisper + a parallel emotion tap.

Model: ``FunAudioLLM/SenseVoiceSmall`` (234 M params, ~70 ms on
Blackwell, low-hundreds-ms on CPU). Lives behind the ``[sensevoice]``
optional extra so the funasr dep stays opt-in.

Per utterance, emits in order:

  1. ``EmotionFrame``      — emotion + confidence + lang + speaker_verified + audio_bytes
  2. ``AudioEventFrame``   — only when non-speech events were detected
  3. ``TranscriptionFrame`` — clean text, same downstream contract as Whisper

The ``speaker_verified`` flag mirrors the most-recent
``OwnerVerifiedFrame`` / ``StrangerDetectedFrame`` from the upstream
``SpeakerGate`` so ``AudioTagsTap`` (Phase 3) can gate mood writes on
owner audio without a separate observer.

References:
- Issue #66 spec: https://github.com/protoLabsAI/ORBIS/issues/66
- FunASR / SenseVoice: https://github.com/FunAudioLLM/SenseVoice
- Frame contracts: agent/frames.py
"""

from __future__ import annotations

import io
import logging
import os
import re
import threading
import time
from collections.abc import AsyncGenerator
from typing import Any

import numpy as np
import soundfile as sf
import soxr
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601

from agent import tracing
from agent.frames import AudioEventFrame, EmotionFrame
from agent.speaker_gate import OwnerVerifiedFrame, StrangerDetectedFrame

logger = logging.getLogger(__name__)


# Model + device defaults — env-overridable.
_DEFAULT_MODEL = os.environ.get(
    "SENSEVOICE_MODEL", "FunAudioLLM/SenseVoiceSmall"
)
_DEFAULT_DEVICE = os.environ.get("SENSEVOICE_DEVICE")  # None → funasr picks


# Allow-list for models we'll load with ``trust_remote_code=True``.
# FunASR / SenseVoice ship custom Python in the model repo; loading
# without trust_remote_code refuses to run that code, which means the
# model can't actually inference. Loading WITH trust_remote_code on a
# user-controlled model id is an RCE vector — a malicious or
# compromised upstream repo would execute arbitrary Python at load
# time. We pin a curated allow-list of repos we've reviewed; a custom
# SENSEVOICE_MODEL outside the list requires explicit acknowledgement
# via SENSEVOICE_TRUST_REMOTE_CODE=1 so the operator owns the risk.
_TRUSTED_REMOTE_CODE_MODELS: frozenset[str] = frozenset({
    "FunAudioLLM/SenseVoiceSmall",
    # Mirror published by the original authors; identical content.
    "iic/SenseVoiceSmall",
})


def _trust_remote_code_for(model_id: str) -> bool:
    """Return True only if it's safe to enable trust_remote_code for
    this model — either a curated entry, or the operator has explicitly
    acknowledged the risk via ``SENSEVOICE_TRUST_REMOTE_CODE=1``."""
    if model_id in _TRUSTED_REMOTE_CODE_MODELS:
        return True
    return os.environ.get("SENSEVOICE_TRUST_REMOTE_CODE", "").lower() in (
        "1", "true", "yes", "on",
    )


# Process-wide model cache so multiple sessions / pipelines share one
# load. Keyed on (model_id, device) so an upgrade or device flip lands
# a fresh model instead of returning the cached old one.
_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_MODEL_LOCK = threading.Lock()


# --- Tag parsing ----------------------------------------------------------

# SenseVoice's tagged output format looks like
#   <|en|><|HAPPY|><|Speech|><|withitn|>Hello world
# These maps cover every documented FunASR tag plus a graceful default
# for unknown tags (treat as no-op rather than crashing the parse).

_LANG_TAGS: frozenset[str] = frozenset({"en", "zh", "ja", "ko", "yue"})

_EMOTION_TAG_MAP: dict[str, str] = {
    "HAPPY": "happy",
    "SAD": "sad",
    "ANGRY": "angry",
    "NEUTRAL": "neutral",
    "FEARFUL": "fearful",
    "DISGUSTED": "disgusted",
    "SURPRISED": "surprised",
    # FunASR sometimes emits this when the head's confident in
    # nothing — surface as neutral rather than dropping the frame.
    "EMO_UNKNOWN": "neutral",
}

# Audio-event tags. ``Speech`` is dropped — it's a tautology on a
# transcription. The other events (BGM, Applause, Laughter, Cry, etc.)
# are signal we want to keep.
_AUDIO_EVENT_TAGS: frozenset[str] = frozenset({
    "BGM", "Applause", "Laughter", "Cry", "Sneeze", "Breath", "Cough",
})

# Markers we explicitly drop (no signal value): ITN flags + Speech.
_NOOP_TAGS: frozenset[str] = frozenset({
    "Speech", "withitn", "woitn",
})

_TAG_RE = re.compile(r"<\|([^|]+)\|>")


def parse_sensevoice_output(raw_text: str) -> tuple[str, str, list[str], str]:
    """Pull (transcription, emotion, events, lang) out of SenseVoice's
    tagged output string.

    Returns:
        transcription: clean text with all ``<|...|>`` tags stripped.
        emotion: one of ``EMOTION_LABELS``; defaults to ``"neutral"``.
        events: list of audio events (``Speech`` filtered out).
        lang: ``en|zh|ja|ko|yue``; defaults to ``"en"``.

    Unknown tags are silently ignored — better to under-report than to
    have a downstream crash on a future FunASR version that adds tags
    we don't recognize yet.
    """
    if not raw_text:
        return "", "neutral", [], "en"

    emotion = "neutral"
    lang = "en"
    events: list[str] = []

    for tag in _TAG_RE.findall(raw_text):
        if tag in _EMOTION_TAG_MAP:
            emotion = _EMOTION_TAG_MAP[tag]
        elif tag in _LANG_TAGS:
            lang = tag
        elif tag in _AUDIO_EVENT_TAGS:
            events.append(tag)
        # Unknown / Speech / ITN markers fall through.

    transcription = _TAG_RE.sub("", raw_text).strip()
    return transcription, emotion, events, lang


# --- Backend --------------------------------------------------------------


class SenseVoiceSTT(SegmentedSTTService):
    """Pipecat STT wrapper around FunASR's SenseVoice-Small."""

    def __init__(
        self,
        *,
        user_id: str = "user",
        model: str = _DEFAULT_MODEL,
        device: str | None = _DEFAULT_DEVICE,
        _loader_factory: Any = None,
        **kwargs,
    ) -> None:
        kwargs.setdefault("settings", STTSettings(model=None, language=None))
        super().__init__(**kwargs)
        self._user_id = user_id
        self._model_id = model
        self._device = device
        self._loader_factory = _loader_factory
        self._model: Any = None
        # Default True so first-utterance frames don't get incorrectly
        # tagged as stranger before the upstream gate has emitted.
        # Owner-trust-by-default matches the gate's own contract.
        self._speaker_verified: bool = True

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Track upstream SpeakerGate decisions so EmotionFrame.speaker_verified
        carries the right value when run_stt fires later in the utterance."""
        if isinstance(frame, OwnerVerifiedFrame):
            self._speaker_verified = True
        elif isinstance(frame, StrangerDetectedFrame):
            self._speaker_verified = False
        await super().process_frame(frame, direction)

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Decode + run SenseVoice + emit frames in spec order.

        The audio buffer is whatever pipecat's SegmentedSTTService
        aggregated between VAD start/end markers — typically WAV.
        We resample to 16 kHz mono since SenseVoice expects that;
        the original bytes ride along on EmotionFrame.audio_bytes for
        downstream taps that want to re-infer (v5 SNR/env/rate heads).
        """
        logger.info(f"[stt.sensevoice] run_stt received audio bytes={len(audio)}")
        try:
            data, sr = sf.read(io.BytesIO(audio), dtype="float32")
        except Exception as e:
            logger.warning(f"[stt.sensevoice] decode failed: {e}")
            yield ErrorFrame(error=f"STT decode failed: {e}")
            return

        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != 16000:
            data = soxr.resample(data, sr, 16000)

        duration_s = len(data) / 16000.0
        rms = float(np.sqrt(np.mean(data * data))) if data.size else 0.0
        logger.info(
            f"[stt.sensevoice] decoded sr_in={sr} samples={data.size} "
            f"duration={duration_s:.2f}s rms={rms:.4f}"
        )

        with tracing.span(
            "stt.sensevoice",
            input={"sample_rate": 16000, "audio_seconds": round(duration_s, 2)},
            metadata={"backend": "sensevoice", "model": self._model_id},
        ) as sp:
            try:
                t0 = time.time()
                model = self._ensure_loaded()
                raw_text = self._invoke(model, data)
                logger.info(
                    f"[stt.sensevoice] {duration_s:.2f}s -> "
                    f"{(time.time() - t0):.2f}s -> {len(raw_text)} chars"
                )
            except Exception as e:
                sp.update(level="ERROR", status_message=str(e))
                logger.error(f"[stt.sensevoice] inference failed: {e}")
                yield ErrorFrame(error=f"STT inference failed: {e}")
                return

            transcription, emotion, events, lang = parse_sensevoice_output(raw_text)
            sp.update(
                output={
                    "transcription": transcription,
                    "emotion": emotion,
                    "events": events,
                    "lang": lang,
                }
            )

        # Emission order matters: emotion arrives BEFORE the transcript
        # so AudioTagsTap can write mood + inject the [audio] system
        # message before the LLM sees the user's words.
        yield EmotionFrame(
            emotion=emotion,
            confidence="medium",  # FunASR doesn't expose calibrated logits
            lang=lang,
            speaker_verified=self._speaker_verified,
            audio_bytes=audio,
        )
        if events:
            yield AudioEventFrame(events=events)
        if transcription:
            yield TranscriptionFrame(
                transcription, self._user_id, time_now_iso8601()
            )
        else:
            logger.info("[stt.sensevoice] empty transcription — no TranscriptionFrame")

    # --- model loading ---------------------------------------------------

    def _ensure_loaded(self) -> Any:
        if self._model is not None:
            return self._model
        if self._loader_factory is not None:
            self._model = self._loader_factory()
            return self._model
        cache_key = (self._model_id, self._device or "auto")
        with _MODEL_LOCK:
            cached = _MODEL_CACHE.get(cache_key)
            if cached is not None:
                self._model = cached
                return cached
            self._model = self._load_funasr()
            _MODEL_CACHE[cache_key] = self._model
        return self._model

    def _load_funasr(self) -> Any:
        try:
            from funasr import AutoModel
        except ImportError as e:
            raise ImportError(
                "funasr is required for SenseVoiceSTT; install via "
                "`pip install -e \".[sensevoice]\"` or set "
                "STT_BACKEND=local to use Whisper."
            ) from e

        # trust_remote_code is gated through _trust_remote_code_for so a
        # custom SENSEVOICE_MODEL that isn't in the curated allow-list
        # refuses to run upstream Python without explicit operator
        # acknowledgement. Without trust_remote_code, FunASR will fail
        # to load SenseVoice — that's the right failure mode: surface
        # the risk to the operator instead of silently executing
        # arbitrary upstream code.
        trust = _trust_remote_code_for(self._model_id)
        if not trust:
            logger.warning(
                f"[stt.sensevoice] {self._model_id!r} is NOT in the "
                "trusted-remote-code allow-list. Loading without "
                "trust_remote_code will likely fail. To explicitly "
                "trust this model, set SENSEVOICE_TRUST_REMOTE_CODE=1 "
                "(this opts you into running arbitrary Python from the "
                "model repo at load time)."
            )
        kwargs: dict[str, Any] = {
            "model": self._model_id,
            "trust_remote_code": trust,
        }
        if self._device:
            kwargs["device"] = self._device
        logger.info(
            f"[stt.sensevoice] loading {self._model_id} "
            f"(device={self._device or 'auto'}, trust_remote_code={trust})"
        )
        return AutoModel(**kwargs)

    @staticmethod
    def _invoke(model: Any, wav: np.ndarray) -> str:
        """Drive SenseVoice. Wrapped so tests can stub without funasr.

        FunASR's ``AutoModel.generate`` returns a list of dicts with a
        ``text`` field. Empty result → empty string (downstream
        tolerates it)."""
        result = model.generate(
            input=wav,
            cache={},
            language="auto",
            use_itn=True,
            batch_size_s=60,
        )
        if not result:
            return ""
        first = result[0]
        if isinstance(first, dict):
            return str(first.get("text") or "")
        return str(first or "")


def prewarm(
    *,
    model: str = _DEFAULT_MODEL,
    device: str | None = _DEFAULT_DEVICE,
) -> None:
    """Load + warm SenseVoice on a 1-second silence buffer so the first
    real utterance doesn't pay model-load cost. Mirrors the Whisper
    prewarm path."""
    inst = SenseVoiceSTT(model=model, device=device)
    try:
        m = inst._ensure_loaded()
    except ImportError as e:
        logger.warning(f"[stt.sensevoice] prewarm skipped: {e}")
        return
    silence = np.zeros(16000, dtype=np.float32)
    try:
        SenseVoiceSTT._invoke(m, silence)
    except Exception as e:
        logger.warning(f"[stt.sensevoice] warm inference raised: {e}")
