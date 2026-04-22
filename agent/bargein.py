"""Adaptive barge-in gate.

LiveKit's production data (2025): ~51 % of VAD-triggered barge-ins are
false positives — coughs, sneezes, background TV, a brief "mm-hmm" from
the user, the cat knocking something over. Raw VAD on the mic will
interrupt the bot for any of those. The bot then stops mid-sentence for
nothing, which reads as glitchy.

BargeInGate inserts a short grace window (default 350 ms) when VAD fires
`UserStartedSpeakingFrame` during bot speech. Within that window:

  - If `UserStoppedSpeakingFrame` arrives → false positive (brief blip,
    shorter than any real start-of-utterance). Suppress the interrupt;
    bot keeps going.
  - If a `TranscriptionFrame` / `InterimTranscriptionFrame` arrives → Whisper
    produced actual words, so it's real speech. Flush the pending start
    frame immediately so downstream can cancel TTS.
  - If the window elapses still ambiguous → flush; proceed as a normal
    interrupt. User loses at most ~350 ms of latency; no worse than the
    raw behaviour.

When the bot is NOT speaking, the gate is a no-op — normal user turns
flow through with zero delay.

Placement: immediately after the user aggregator, before any processor
that reacts to `UserStartedSpeakingFrame` (backchannel, delivery, LLM,
TTS). See app.py pipeline construction.
"""

from __future__ import annotations

import asyncio
import logging

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


class BargeInGate(FrameProcessor):
    """Buffer UserStartedSpeakingFrame during bot speech; release or
    suppress based on what follows within a short grace window."""

    def __init__(self, *, grace_ms: int = 350, enabled: bool = True) -> None:
        super().__init__()
        self._grace_s = grace_ms / 1000.0
        self._enabled = enabled
        self._bot_speaking = False
        self._pending: UserStartedSpeakingFrame | None = None
        self._pending_dir: FrameDirection | None = None
        self._timer: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Track bot speaking state so we only gate during its turn.
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            # Bot is done — nothing left to interrupt. Flush any pending
            # start so downstream sees a normal user-turn beginning.
            await self._flush()

        # Gate only when enabled and the bot is actually speaking.
        if (
            self._enabled
            and isinstance(frame, UserStartedSpeakingFrame)
            and self._bot_speaking
            and self._pending is None
        ):
            self._pending = frame
            self._pending_dir = direction
            self._timer = asyncio.create_task(self._on_grace_expired())
            return  # withhold the frame for now

        # Short blip during the grace window → false positive, swallow.
        if isinstance(frame, UserStoppedSpeakingFrame) and self._pending is not None:
            logger.info("[bargein] rejected false positive (<= grace window)")
            self._cancel_timer()
            self._pending = None
            self._pending_dir = None
            return  # swallow — do NOT emit UserStopped for a never-emitted start

        # Real words arrived — it IS speech. Let the pending start through.
        if (
            isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame))
            and self._pending is not None
        ):
            logger.info("[bargein] transcription confirmed — releasing interrupt")
            await self._flush()

        await self.push_frame(frame, direction)

    async def _on_grace_expired(self) -> None:
        try:
            await asyncio.sleep(self._grace_s)
            if self._pending is not None:
                logger.info("[bargein] grace elapsed — releasing interrupt")
                await self._flush()
        except asyncio.CancelledError:
            pass

    async def _flush(self) -> None:
        if self._pending is not None and self._pending_dir is not None:
            await self.push_frame(self._pending, self._pending_dir)
        self._pending = None
        self._pending_dir = None
        self._cancel_timer()

    def _cancel_timer(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None
