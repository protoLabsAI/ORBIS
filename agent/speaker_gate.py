"""Speaker-verification gate — Phase 1 perception layer (foundation only).

ORBIS is single-owner; the orb should know when *someone else* is talking.
This processor sits between EchoGuardSuppressor and STT in the pipeline,
buffers per-utterance audio, computes a speaker embedding when the user
stops speaking, and cosine-compares against a cached owner voiceprint.

Outcome frames:
  - ``OwnerVerifiedFrame`` — high similarity, audio is the registered owner
  - ``StrangerDetectedFrame`` — low similarity, with the configured action

Downstream consumers (audio-tags writer, personality drift, facts table)
key off these frames so guest voices don't pollute owner state.

This module is the **foundation** — it ships the gate logic, the
``Embedder`` protocol, the voiceprint persistence helpers, and the
cosine helper. The actual speechbrain ECAPA loader and pipeline wiring
land in follow-up PRs once the design holds up to review.

References:
- Issue #35 spec: https://github.com/protoLabsAI/ORBIS/issues/35
- Companion-stack research: protoLabsAI/protoLab → experiments/companion-stack
- Lifecycle audit: docs/voice-lifecycle.md (Stage 4 — STT, audio-pre slot)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

import numpy as np
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


# --- Frame types ----------------------------------------------------------


class StrangerAction(str, Enum):
    """How the system should react to a stranger. The gate doesn't
    enforce — it just labels the frame so consumers can branch."""
    WARN = "warn"
    REFUSE = "refuse"
    DELEGATE_GUEST = "delegate_guest"


@dataclass
class OwnerVerifiedFrame(Frame):
    """Emitted on UserStoppedSpeaking when speaker embedding cosine-
    similarity to the cached owner voiceprint exceeds the threshold."""
    score: float = 0.0


@dataclass
class StrangerDetectedFrame(Frame):
    """Emitted when the score is below threshold. ``action`` is the
    configured stranger_action — downstream processors decide what to do
    (warn the user, refuse the turn, route to a guest-handler delegate)."""
    score: float = 0.0
    action: StrangerAction = StrangerAction.WARN


# --- Voiceprint persistence ----------------------------------------------


def load_voiceprint(path: str | Path) -> np.ndarray | None:
    """Load a cached voiceprint from disk. Returns None when missing —
    that triggers owner-trust fallback in the gate. Returns None (with
    a warning log) when the file exists but is malformed; caller must
    decide whether to proceed in fallback mode or refuse to start."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        emb = np.load(p)
    except Exception as e:
        logger.warning(f"[speaker_gate] voiceprint at {p} unreadable: {e}")
        return None
    if emb.ndim != 1:
        logger.warning(
            f"[speaker_gate] voiceprint at {p} is {emb.ndim}-d; expected 1-d"
        )
        return None
    return emb.astype(np.float32, copy=False)


def save_voiceprint(path: str | Path, embedding: np.ndarray) -> None:
    """Save a 1-d embedding atomically. Writes to ``<path>.tmp`` then
    rename so an interrupted save doesn't leave a half-written file."""
    if embedding.ndim != 1:
        raise ValueError(
            f"voiceprint must be 1-d; got shape {embedding.shape}"
        )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    np.save(tmp, embedding.astype(np.float32, copy=False))
    # np.save tacks on .npy if it isn't already there. Rename the
    # ACTUAL file it wrote.
    written = tmp if tmp.exists() else tmp.with_suffix(tmp.suffix + ".npy")
    written.replace(p)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-d vectors. Returns 0.0 when
    either has zero magnitude — silent handling for the degenerate case
    a downstream gate doesn't want to crash on."""
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# --- Embedder protocol ---------------------------------------------------


class Embedder(Protocol):
    """Anything that turns a wav buffer into a 1-d speaker embedding.

    The real implementation will wrap speechbrain's ECAPA-TDNN
    (``speechbrain/spkrec-ecapa-voxceleb``) — ~6 M params, 192-dim
    output, ~50 ms CPU / ~5 ms GPU. Lives in a follow-up PR.

    Tests inject a ``MockEmbedder`` so the gate logic is exercisable
    without speechbrain.
    """

    def encode(self, wav: np.ndarray, sample_rate: int) -> np.ndarray:
        ...


# --- Gate -----------------------------------------------------------------


class SpeakerGate(FrameProcessor):
    """Per-utterance speaker verification.

    Buffers ``InputAudioRawFrame`` audio between ``UserStartedSpeakingFrame``
    and ``UserStoppedSpeakingFrame``. On stop, encodes the buffer and
    cosine-compares against the owner voiceprint:

      score >= threshold  → emit OwnerVerifiedFrame
      score <  threshold  → emit StrangerDetectedFrame(action=...)

    When ``voiceprint`` is None (no enrollment yet), runs in
    **owner-trust** mode — emits ``OwnerVerifiedFrame(score=1.0)`` for
    every utterance. Preserves the no-auth single-user deployment story.

    The gate ALWAYS forwards the original frames downstream so VAD /
    STT see the same audio as before. The verification frame rides
    alongside, not replacing.
    """

    def __init__(
        self,
        *,
        embedder: Embedder | None = None,
        voiceprint: np.ndarray | None = None,
        threshold: float = 0.62,
        stranger_action: StrangerAction = StrangerAction.WARN,
        enabled: bool = True,
    ) -> None:
        super().__init__()
        self._embedder = embedder
        self._voiceprint = voiceprint
        self._threshold = threshold
        self._action = stranger_action
        self._enabled = enabled
        self._buf: list[bytes] = []
        self._speaking: bool = False
        if not enabled:
            logger.info("[speaker_gate] disabled — passthrough")
        elif voiceprint is None:
            logger.info("[speaker_gate] no voiceprint — owner-trust mode")
        elif embedder is None:
            logger.warning(
                "[speaker_gate] enabled with voiceprint but no embedder; "
                "running in owner-trust fallback"
            )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if self._enabled:
            if isinstance(frame, UserStartedSpeakingFrame):
                self._buf = []
                self._speaking = True
            elif isinstance(frame, InputAudioRawFrame):
                # Only buffer between Started/Stopped — passthrough during
                # idle/echo-guard windows so we don't accumulate forever.
                if self._speaking:
                    self._buf.append((frame.audio, frame.sample_rate))
            elif isinstance(frame, UserStoppedSpeakingFrame):
                self._speaking = False
                await self._verify_and_emit()

        await self.push_frame(frame, direction)

    async def _verify_and_emit(self) -> None:
        """Run verification on the current buffer, emit the result frame.
        Owner-trust fallback when we lack the means to verify."""
        if self._voiceprint is None or self._embedder is None or not self._buf:
            await self.push_frame(
                OwnerVerifiedFrame(score=1.0), FrameDirection.DOWNSTREAM
            )
            self._buf = []
            return

        # Decode int16 PCM → float32 mono. Each buf entry is (audio_bytes, sample_rate).
        # Use the sample_rate from the first frame; all frames in one utterance
        # share the same transport sample rate.
        try:
            audio_chunks, sample_rates = zip(*self._buf)
            sample_rate = sample_rates[0]
            joined = b"".join(audio_chunks)
            wav = np.frombuffer(joined, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception as e:
            logger.warning(f"[speaker_gate] decode failed: {e} — owner-trust")
            await self.push_frame(
                OwnerVerifiedFrame(score=1.0), FrameDirection.DOWNSTREAM
            )
            self._buf = []
            return

        try:
            emb = self._embedder.encode(wav, sample_rate=sample_rate)
        except Exception as e:
            logger.warning(f"[speaker_gate] embed failed: {e} — owner-trust")
            await self.push_frame(
                OwnerVerifiedFrame(score=1.0), FrameDirection.DOWNSTREAM
            )
            self._buf = []
            return

        score = cosine_similarity(emb, self._voiceprint)
        self._buf = []

        if score >= self._threshold:
            logger.info(f"[speaker_gate] owner verified (score={score:.3f})")
            await self.push_frame(
                OwnerVerifiedFrame(score=score), FrameDirection.DOWNSTREAM
            )
        else:
            logger.info(
                f"[speaker_gate] stranger (score={score:.3f} < {self._threshold:.3f}); "
                f"action={self._action.value}"
            )
            await self.push_frame(
                StrangerDetectedFrame(score=score, action=self._action),
                FrameDirection.DOWNSTREAM,
            )
