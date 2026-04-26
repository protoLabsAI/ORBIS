"""AudioTagsTap — per-turn audio-context writer (#66 Phase 3).

Subscribes to the perception-layer frames produced by the upstream
``SpeakerGate`` and ``SenseVoiceSTT``:

    OwnerVerifiedFrame / StrangerDetectedFrame  (from SpeakerGate)
    EmotionFrame                                 (from SenseVoiceSTT)
    AudioEventFrame                              (from SenseVoiceSTT, sparse)
    TranscriptionFrame                           (from SenseVoiceSTT, last)

For owner-verified audio, applies a per-emotion mood delta via
``mem.personality.drift_mood`` so the orb's tone register adapts to
the user's affect over the session. Strangers are not allowed to
nudge owner mood — that gating is the load-bearing reason
``EmotionFrame.speaker_verified`` exists.

Before each ``TranscriptionFrame`` it injects an ``LLMMessagesAppendFrame``
with the ``[audio]`` annotation as a system message::

    [audio] emotion=happy lang=en speaker=owner events=Laughter,BGM

so the LLM sees audio context BEFORE the user's words. The LLM is
taught how to use this signal (and warned never to parrot it back) by
``audio_context_block`` in the persona prompt.

Composes with the rest of the mood-write three-writer pattern (see
``memory/personality.py``):

  - ``set_mood``           — operator override (drawer UI, tests)
  - ``drift_mood_toward``  — session-open shifts (apply_soft_neglect)
  - ``drift_mood``         — per-turn shifts (THIS module)

References:
- Issue #66: https://github.com/protoLabsAI/ORBIS/issues/66
- Frame contracts: agent/frames.py
- Lifecycle audit: docs/voice-lifecycle.md
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pipecat.frames.frames import (
    Frame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent.frames import AudioEventFrame, EmotionFrame
from agent.speaker_gate import OwnerVerifiedFrame, StrangerDetectedFrame

logger = logging.getLogger(__name__)


# Emotion → (Δvalence, Δarousal) deltas, per the table in #66's spec.
# Tuned so a string of consistent-emotion turns moves mood visibly
# (over ~3-5 turns) without any single turn dominating. neutral and
# absent-emotion produce no write — the no-op contract on
# ``drift_mood`` makes this cheap.
_EMOTION_DELTAS: dict[str, tuple[float, float]] = {
    "happy":     (+0.10, +0.05),
    "surprised": ( 0.00, +0.10),
    "neutral":   ( 0.00,  0.00),
    "sad":       (-0.10, -0.05),
    "fearful":   (-0.05, +0.10),
    "angry":     (-0.15, +0.15),
    "disgusted": (-0.10, +0.05),
}


def _audio_annotation(
    emotion: EmotionFrame | None,
    events: list[str],
) -> str | None:
    """Format the ``[audio]`` annotation that gets injected as a
    system message before each user turn. Returns None when there's
    no signal worth surfacing — empty annotations would teach the LLM
    nothing and waste prompt tokens."""
    if emotion is None:
        return None
    speaker = "owner" if emotion.speaker_verified else "stranger"
    parts = [
        f"emotion={emotion.emotion}",
        f"lang={emotion.lang}",
        f"speaker={speaker}",
    ]
    if events:
        parts.append("events=" + ",".join(events))
    return "[audio] " + " ".join(parts)


class AudioTagsTap(FrameProcessor):
    """Per-turn mood writer + ``[audio]`` system-message injector."""

    def __init__(
        self,
        *,
        mem: Any,
        enabled: bool = True,
    ) -> None:
        super().__init__()
        self._mem = mem
        self._enabled = enabled
        self._latest_emotion: EmotionFrame | None = None
        self._latest_events: list[str] = []
        if not enabled:
            logger.info("[audio_tags] disabled — passthrough only")

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Only act on DOWNSTREAM frames — upstream control frames pass
        # through unchanged. Same gate the SpeakerGate uses.
        if not self._enabled or direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, EmotionFrame):
            self._latest_emotion = frame
            # AudioEventFrame is per-utterance; reset on each new
            # emotion (which marks a new utterance window).
            self._latest_events = []
            self._maybe_write_mood(frame)
        elif isinstance(frame, AudioEventFrame):
            self._latest_events = list(frame.events)
        elif isinstance(frame, OwnerVerifiedFrame):
            # The verification flag rides on EmotionFrame.speaker_verified
            # too (mirrored from upstream by SenseVoiceSTT), but track
            # it here as well so deployments running Whisper STT (no
            # EmotionFrame) can still read the gate decision if a
            # future tap needs it.
            if self._latest_emotion is not None:
                self._latest_emotion.speaker_verified = True
        elif isinstance(frame, StrangerDetectedFrame):
            if self._latest_emotion is not None:
                self._latest_emotion.speaker_verified = False
        elif isinstance(frame, TranscriptionFrame):
            await self._inject_audio_annotation(direction)

        await self.push_frame(frame, direction)

    def _maybe_write_mood(self, frame: EmotionFrame) -> None:
        """Apply the per-emotion mood delta. Owner-verified only —
        a stranger's affect must not nudge the owner's mood register
        (the whole reason EmotionFrame carries speaker_verified)."""
        if not frame.speaker_verified:
            return
        deltas = _EMOTION_DELTAS.get(frame.emotion)
        if deltas is None:
            logger.debug(
                f"[audio_tags] no delta for emotion={frame.emotion!r} "
                "(unknown or off-taxonomy); skipping mood write"
            )
            return
        dv, da = deltas
        if dv == 0.0 and da == 0.0:
            return  # neutral — drift_mood would no-op anyway, but skip the call
        try:
            self._mem.personality.drift_mood(
                valence_delta=dv,
                arousal_delta=da,
            )
            logger.info(
                f"[audio_tags] mood drift Δv={dv:+.2f} Δa={da:+.2f} "
                f"(emotion={frame.emotion})"
            )
        except Exception as e:
            # Don't let a memory write blow up the frame loop —
            # mood writes are best-effort. Log loud so the operator
            # notices repeated failures.
            logger.warning(f"[audio_tags] drift_mood raised: {e}")

    async def _inject_audio_annotation(
        self, direction: FrameDirection
    ) -> None:
        """Push an LLMMessagesAppendFrame with the ``[audio]`` line as
        a system message. Lands BEFORE the TranscriptionFrame because
        we're called inside process_frame for the TranscriptionFrame —
        the push happens here, then the original frame is pushed by
        process_frame's bottom-of-method push.
        ``run_llm=False`` so we don't trigger an LLM run on the
        annotation alone — the TranscriptionFrame that follows is
        what fires the response."""
        annotation = _audio_annotation(self._latest_emotion, self._latest_events)
        if annotation is None:
            return
        await self.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "system", "content": annotation}],
                run_llm=False,
            ),
            direction,
        )


def make_audio_tags_tap(*, mem: Any) -> AudioTagsTap:
    """Construct an AudioTagsTap with the env-driven enabled flag.

    ``AUDIO_TAGS=off`` disables the tap (passthrough). Default is
    enabled — when STT_BACKEND=local (no EmotionFrame source) the tap
    just doesn't do anything because no EmotionFrame ever arrives, so
    leaving it on costs nothing.
    """
    enabled = os.environ.get("AUDIO_TAGS", "on").lower() not in (
        "off", "0", "false", "no",
    )
    return AudioTagsTap(mem=mem, enabled=enabled)
