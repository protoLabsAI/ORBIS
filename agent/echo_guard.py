"""Echo / feedback guard.

Two related behaviours, opted in via env:

  HALF_DUPLEX=1
    While the bot is speaking, drop incoming InputAudioRawFrame entirely.
    The mic is effectively muted during agent TTS playback. Loses
    real-time barge-in (user has to wait for the bot to finish), but
    eliminates echo loops on noisy hardware setups.

  ECHO_GUARD_MS=300  (default)
    For this many milliseconds after the bot stops speaking, drop
    incoming audio. Catches the tail of bot audio bleeding back through
    speakers + mic that the browser AEC missed. Cheap, safe default.

Both compose. Half-duplex covers the in-flight window; echo-guard covers
the immediate post-window. Together they kill the most common
"bot interrupts itself" failure mode without any DSP work.

Real AEC with a TTS reference signal is out of scope here — see
docs/guides/audio-handling.md for the heavier paths (rnnoise, smart-turn,
SpeexDSP).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

HALF_DUPLEX = os.environ.get("HALF_DUPLEX", "0") == "1"
ECHO_GUARD_MS = int(os.environ.get("ECHO_GUARD_MS", "300"))


@dataclass
class EchoGuardState:
    """Shared state between the observer (writes) and the suppressor
    (reads). Module-level so a single state instance survives connections."""
    half_duplex: bool = HALF_DUPLEX
    guard_ms: int = ECHO_GUARD_MS
    bot_speaking: bool = False
    bot_stopped_at: float | None = None


class EchoGuardObserver(BaseObserver):
    """Watches every frame in the pipeline; updates EchoGuardState when
    the bot starts / stops speaking. Observers don't transform — they
    just see, which is exactly what we need to track distant frames
    (BotStartedSpeakingFrame originates at transport.output, far from
    where we want to drop input audio)."""

    def __init__(self, state: EchoGuardState):
        super().__init__()
        self._state = state

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if isinstance(frame, BotStartedSpeakingFrame):
            self._state.bot_speaking = True
            self._state.bot_stopped_at = None
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._state.bot_speaking = False
            self._state.bot_stopped_at = time.time()


class EchoGuardSuppressor(FrameProcessor):
    """Drops InputAudioRawFrames during the guard window. Place
    immediately after transport.input() so VAD downstream never even
    sees the suppressed audio — no false UserStartedSpeakingFrame from
    echo bleed."""

    def __init__(self, state: EchoGuardState):
        super().__init__()
        self._state = state
        self._suppressing_now = False  # for log de-dup

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and self._should_drop():
            if not self._suppressing_now:
                logger.info(
                    f"[echoguard] suppressing audio "
                    f"(half_duplex={self._state.half_duplex} "
                    f"bot_speaking={self._state.bot_speaking} "
                    f"guard_ms={self._state.guard_ms})"
                )
                self._suppressing_now = True
            return
        if self._suppressing_now and not self._should_drop():
            logger.info("[echoguard] resuming audio")
            self._suppressing_now = False
        await self.push_frame(frame, direction)

    def _should_drop(self) -> bool:
        if self._state.half_duplex and self._state.bot_speaking:
            return True
        if self._state.bot_stopped_at is None:
            return False
        return (time.time() - self._state.bot_stopped_at) * 1000 < self._state.guard_ms
