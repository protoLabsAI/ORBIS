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

Deferred design calls (must land before PR 1.2 wires the gate into
the pipeline — wiring without resolution risks shipping latent bugs):

  - **R15 — soft-neglect vs audio-tags mood collision**: ``apply_soft_neglect``
    in ``agent/neglect.py`` SETS ``personality.mood`` at session-open.
    PR 2 (audio-tags side-channel) will write mood per-turn from the
    owner's audio. The two systems collide; reconcile to "neglect sets
    a baseline, audio-tags drifts from it" before PR 1.2 wires the gate.
  - **Ambiguity-zone policy**: scores in 0.5–0.7 are borderline; the
    gate currently treats every sub-threshold score the same (single
    StrangerAction). PR 1.2 will need a 3-state policy
    (verified / ambiguous / stranger) or a hysteresis band.
  - **EnrollmentRequiredFrame**: today owner-trust-because-no-voiceprint
    and owner-trust-because-disabled both surface as
    ``OwnerVerifiedFrame(score=1.0)`` — downstream can't tell them
    apart. The wizard / drawer UI will need a distinct frame to
    surface "enroll your voice" vs "speaker-id is off".

References:
- Issue #35 spec: https://github.com/protoLabsAI/ORBIS/issues/35
- Companion-stack research: protoLabsAI/protoLab → experiments/companion-stack
- Lifecycle audit: docs/voice-lifecycle.md (Stage 4 — STT, audio-pre slot)
- Risks doc: docs/voice-lifecycle-risks.md (R15)
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
    enforce — it just labels the frame so consumers can branch.

    Inherits from str so the enum value compares equal to its underlying
    string ("warn" == StrangerAction.WARN holds), letting downstream
    consumers switch on either form. Frames carry the enum directly to
    avoid the parse-string-back-to-enum round-trip a plain `str` field
    would force.
    """
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
        # Buffer is only filled while _speaking is True (between
        # UserStartedSpeakingFrame and UserStoppedSpeakingFrame). Without
        # this guard, audio frames arriving outside an utterance window
        # (echo-guard tail, post-stop drain) leak into the next encode,
        # and a degenerate VAD state with two consecutive Stopped frames
        # encodes stale audio from the prior turn.
        self._speaking: bool = False
        self._buf: list[bytes] = []
        # Captured from the first buffered frame each utterance. The
        # transport delivers audio at whatever rate it negotiated with
        # the browser (typically 16k or 48k). Forwarding the real SR to
        # the embedder is load-bearing: ECAPA-TDNN is trained on 16k —
        # feeding it 48k PCM as if it were 16k yields garbage embeddings,
        # which the trust-fallback then promotes to OwnerVerifiedFrame.
        self._buf_sample_rate: int = 0
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

        # Only buffer DOWNSTREAM frames — the gate sits between
        # transport.input and STT, so input audio always travels
        # downstream. Upstream frames (e.g. control frames coming back
        # from the LLM/TTS path) must pass through unchanged without
        # touching the buffer.
        if self._enabled and direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, UserStartedSpeakingFrame):
                self._speaking = True
                self._buf = []
                self._buf_sample_rate = 0
            elif isinstance(frame, InputAudioRawFrame) and self._speaking:
                # First frame of the utterance pins the SR for the
                # entire encode. The transport doesn't change SR
                # mid-utterance; later frames at a different SR would
                # indicate a transport bug we don't want to silently
                # accommodate by averaging.
                if self._buf_sample_rate == 0:
                    self._buf_sample_rate = int(frame.sample_rate)
                self._buf.append(frame.audio)
            elif isinstance(frame, UserStoppedSpeakingFrame) and self._speaking:
                self._speaking = False
                await self._verify_and_emit()

        await self.push_frame(frame, direction)

    async def _verify_and_emit(self) -> None:
        """Run verification on the current buffer, emit the result frame.
        Owner-trust fallback when we lack the means to verify."""
        sample_rate = self._buf_sample_rate
        # Reset buffer state up-front so any return path leaves a clean
        # slate for the next utterance.
        buf, self._buf = self._buf, []
        self._buf_sample_rate = 0

        if self._voiceprint is None or self._embedder is None or not buf:
            await self.push_frame(
                OwnerVerifiedFrame(score=1.0), FrameDirection.DOWNSTREAM
            )
            return

        # Decode int16 PCM → float32 mono. The encoder will resample if
        # its expected SR differs from sample_rate; we forward the SR
        # captured from the first buffered frame.
        try:
            joined = b"".join(buf)
            wav = np.frombuffer(joined, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception as e:
            logger.warning(f"[speaker_gate] decode failed: {e} — owner-trust")
            await self.push_frame(
                OwnerVerifiedFrame(score=1.0), FrameDirection.DOWNSTREAM
            )
            return

        try:
            emb = self._embedder.encode(wav, sample_rate=sample_rate)
        except Exception as e:
            logger.warning(f"[speaker_gate] embed failed: {e} — owner-trust")
            await self.push_frame(
                OwnerVerifiedFrame(score=1.0), FrameDirection.DOWNSTREAM
            )
            return

        score = cosine_similarity(emb, self._voiceprint)

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
