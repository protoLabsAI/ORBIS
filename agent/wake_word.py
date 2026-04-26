"""Wake-word gate — pre-STT audio filter using openWakeWord.

Sits immediately after ``transport.input()`` in the pipeline. While the
orb is **sleeping**, ``InputAudioRawFrame`` chunks are silently dropped
so they never reach the STT layer. When the configured phrase is detected,
the gate transitions to **awake**, emits a ``WakeWordFrame``, and passes
all subsequent frames through normally until the inactivity timeout fires.

Audio format: the SmallWebRTCTransport delivers 16-bit PCM at 16 kHz (same
rate Whisper and SenseVoice already use), which is exactly what openWakeWord
expects. No resampling needed.

Chunk sizing: openWakeWord wants multiples of 80 ms (1 280 samples @ 16 kHz).
The transport sends 20 ms chunks (320 samples). We accumulate four chunks
before running inference — minimum latency, no padding waste.

Custom models
-------------
Point ``model_path`` at any ``.tflite`` or ``.onnx`` model produced by the
openWakeWord training notebook and the gate loads it transparently. The
pre-trained names (``"hey jarvis"``, ``"alexa"``, etc.) still work when
no ``model_path`` is given.

Training a custom "hey orbis" model:
  1. Open the openWakeWord Colab notebook (see scripts/train_wake_word.md).
  2. Export as ``hey_orbis.tflite``.
  3. Set ``WAKE_WORD_MODEL=data/wake_word/hey_orbis.tflite`` (or
     ``behavior.wake_word.model_path`` in orbis.yaml).

Installation
------------
    pip install -e ".[wake-word]"

References:
- Issue #95
- Roadmap: companion-stack Phase 4 — audio-pre/wake-word
- docs: scripts/train_wake_word.md
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent.frames import WakeWordFrame

logger = logging.getLogger(__name__)

# openWakeWord expects 16-bit PCM at this rate.
_EXPECTED_SAMPLE_RATE = 16_000
# Inference window: 80 ms = 1 280 samples. Transport sends 20 ms (320
# samples) chunks, so we accumulate 4 before running predict().
_SAMPLES_PER_WINDOW = 1_280


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class WakeWordConfig:
    """Resolved configuration for ``WakeWordDetector``.

    Build via ``WakeWordConfig.from_env()`` or ``WakeWordConfig.from_dict()``.
    """

    # Path to a custom .tflite / .onnx model. Takes precedence over ``name``.
    model_path: Optional[str] = None
    # Pre-trained model name (e.g. "hey jarvis"). Used when model_path is None.
    name: str = "hey jarvis"
    # Confidence score threshold (0–1).
    threshold: float = 0.5
    # Seconds of inactivity (no speech, no bot audio) before returning to sleep.
    timeout: float = 30.0
    # Whether the gate is enabled at all.
    enabled: bool = False

    @classmethod
    def from_env(cls) -> "WakeWordConfig":
        """Read config from env vars.

        WAKE_WORD_MODEL   — path to custom .tflite/.onnx
        WAKE_WORD         — pre-trained model name (default: hey jarvis)
        WAKE_WORD_THRESHOLD
        WAKE_WORD_TIMEOUT
        """
        model_path = os.environ.get("WAKE_WORD_MODEL") or None
        name = os.environ.get("WAKE_WORD", "hey jarvis")
        enabled = bool(model_path or os.environ.get("WAKE_WORD"))
        try:
            threshold = float(os.environ.get("WAKE_WORD_THRESHOLD", "0.5"))
        except (TypeError, ValueError):
            logger.warning("[wake_word] invalid WAKE_WORD_THRESHOLD; using 0.5")
            threshold = 0.5
        try:
            timeout = float(os.environ.get("WAKE_WORD_TIMEOUT", "30"))
        except (TypeError, ValueError):
            logger.warning("[wake_word] invalid WAKE_WORD_TIMEOUT; using 30")
            timeout = 30.0
        return cls(
            model_path=model_path,
            name=name,
            threshold=threshold,
            timeout=timeout,
            enabled=enabled,
        )

    @classmethod
    def from_dict(cls, cfg: dict) -> "WakeWordConfig":
        """Build from a ``behavior.wake_word`` config block.

        The block can be ``False`` / ``{"enabled": false}`` to disable,
        or a dict with any subset of the fields.
        """
        if not cfg.get("enabled", True):
            return cls(enabled=False)
        model_path = cfg.get("model_path") or None
        name = cfg.get("name", "hey jarvis")
        enabled = True
        try:
            threshold = float(cfg.get("threshold", 0.5))
        except (TypeError, ValueError):
            logger.warning("[wake_word] invalid threshold in config; using 0.5")
            threshold = 0.5
        try:
            timeout = float(cfg.get("timeout", 30.0))
        except (TypeError, ValueError):
            logger.warning("[wake_word] invalid timeout in config; using 30")
            timeout = 30.0
        return cls(
            model_path=model_path,
            name=name,
            threshold=threshold,
            timeout=timeout,
            enabled=enabled,
        )


# ---------------------------------------------------------------------------
# FrameProcessor
# ---------------------------------------------------------------------------


class WakeWordDetector(FrameProcessor):
    """Pre-STT wake-word gate using openWakeWord.

    States
    ------
    sleeping  — ``InputAudioRawFrame`` chunks are silently dropped;
                all other frame types (control, metadata, system) pass through
                so the rest of the pipeline stays healthy.
    awake     — all frames pass through normally; inactivity timer running.
                Returns to sleeping when ``_timeout`` seconds elapse with no
                ``InputAudioRawFrame`` activity.

    The detector emits a ``WakeWordFrame`` on every transition sleeping→awake.
    Repeated detections within the awake window are silently skipped (debounce).
    """

    def __init__(self, cfg: WakeWordConfig) -> None:
        super().__init__()
        self._cfg = cfg
        self._awake = False
        # Sample accumulator for the current 80 ms window.
        self._buf: list[np.ndarray] = []
        self._buf_samples: int = 0
        # Inactivity timer task.
        self._timeout_task: Optional[asyncio.Task] = None
        # openWakeWord Model instance — lazily loaded on first audio frame
        # so the cold-start cost doesn't hit the import path.
        self._oww_model = None
        # The model key we'll check in the prediction dict (set after load).
        self._model_key: Optional[str] = None

        if not cfg.enabled:
            logger.info("[wake_word] disabled — passthrough")
        else:
            src = cfg.model_path or f"pre-trained:{cfg.name!r}"
            logger.info(
                f"[wake_word] enabled — model={src}, "
                f"threshold={cfg.threshold}, timeout={cfg.timeout}s"
            )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def awake(self) -> bool:
        return self._awake

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if not self._cfg.enabled or direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputAudioRawFrame):
            await self._handle_audio(frame)
        else:
            # Non-audio frames always pass through.
            await self.push_frame(frame, direction)

    # ------------------------------------------------------------------
    # Audio handling
    # ------------------------------------------------------------------

    async def _handle_audio(self, frame: InputAudioRawFrame) -> None:
        """Accumulate samples; run inference when a full window is ready."""
        if self._awake:
            # Awake — pass through and refresh the inactivity timer.
            self._refresh_timeout()
            await self.push_frame(frame, FrameDirection.DOWNSTREAM)
            return

        # Sleeping — accumulate into the 80 ms window.
        samples = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float32) / 32768.0
        self._buf.append(samples)
        self._buf_samples += len(samples)

        # Drain all full windows from the buffer in one pass so that a
        # large frame (e.g. 2× window) runs inference twice without
        # losing the second window to the next audio chunk.
        while self._buf_samples >= _SAMPLES_PER_WINDOW and not self._awake:
            await self._run_inference()

    async def _run_inference(self) -> None:
        """Run openWakeWord on the current buffer and reset it."""
        window = np.concatenate(self._buf)[: _SAMPLES_PER_WINDOW]
        # Keep any overflow samples for the next window.
        overflow = np.concatenate(self._buf)[_SAMPLES_PER_WINDOW:]
        self._buf = [overflow] if len(overflow) else []
        self._buf_samples = len(overflow)

        model = self._load_model()
        if model is None:
            return

        try:
            predictions: dict = model.predict(window)
        except Exception as e:
            logger.warning(f"[wake_word] predict error: {e}")
            return

        # Find the highest-scoring key that clears threshold.
        best_phrase, best_score = self._best_prediction(predictions)
        if best_score >= self._cfg.threshold:
            logger.info(
                f"[wake_word] detected {best_phrase!r} (score={best_score:.3f})"
            )
            await self._transition_awake(best_phrase, best_score)

    def _best_prediction(self, predictions: dict) -> tuple[str, float]:
        """Return (phrase, score) for the highest-scoring prediction."""
        best_phrase = ""
        best_score = 0.0
        for phrase, score in predictions.items():
            if isinstance(score, (int, float)) and score > best_score:
                best_phrase = phrase
                best_score = float(score)
        return best_phrase, best_score

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    async def _transition_awake(self, phrase: str, score: float) -> None:
        self._awake = True
        self._buf = []
        self._buf_samples = 0
        await self.push_frame(
            WakeWordFrame(phrase=phrase, score=score),
            FrameDirection.DOWNSTREAM,
        )
        self._refresh_timeout()

    def _transition_sleeping(self) -> None:
        logger.info("[wake_word] inactivity timeout — returning to sleep")
        self._awake = False
        self._buf = []
        self._buf_samples = 0
        self._timeout_task = None

    # ------------------------------------------------------------------
    # Inactivity timer
    # ------------------------------------------------------------------

    def _refresh_timeout(self) -> None:
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        loop = asyncio.get_event_loop()
        self._timeout_task = loop.create_task(self._timeout_coro())

    async def _timeout_coro(self) -> None:
        try:
            await asyncio.sleep(self._cfg.timeout)
            self._transition_sleeping()
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Model loading (lazy)
    # ------------------------------------------------------------------

    def _load_model(self):
        """Lazily load the openWakeWord model. Returns None on failure."""
        if self._oww_model is not None:
            return self._oww_model
        try:
            from openwakeword.model import Model  # type: ignore[import]
        except ImportError:
            logger.error(
                "[wake_word] openwakeword not installed. "
                'Run: pip install -e ".[wake-word]"'
            )
            return None

        if self._cfg.model_path:
            path = Path(self._cfg.model_path)
            if not path.exists():
                logger.error(
                    f"[wake_word] custom model not found: {path}. "
                    "Check WAKE_WORD_MODEL / behavior.wake_word.model_path."
                )
                return None
            logger.info(f"[wake_word] loading custom model: {path}")
            self._oww_model = Model(
                wakeword_models=[str(path)],
                inference_framework="tflite" if path.suffix == ".tflite" else "onnx",
            )
        else:
            logger.info(f"[wake_word] loading pre-trained model: {self._cfg.name!r}")
            self._oww_model = Model(wakeword_models=[self._cfg.name])

        return self._oww_model
