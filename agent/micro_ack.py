"""Acoustic micro-ack injector.

Vapi's "Fill Injection" pattern. When the user stops speaking, start a
short timer. If the main pipeline (STT → LLM → TTS) hasn't produced
audio by the time the timer expires, emit a tiny acknowledgement (`mm`,
`mhm`, `hm`, `okay`). Gives the user a sense of "I heard you" without
waiting for the full response.

Our baseline TTFA is ~500 ms, so the threshold sits at 500 ms — fast
responses cancel the timer before it fires and no ack plays. Slow
responses (tool calls, large prompts, model cold-start) trigger the
ack to bridge the silence.

Emission goes through `TTSSpeakFrame(append_to_context=False)` so the
ack:
  - never enters LLM context
  - serialises through TTS *before* the main response frames, no overlap
  - respects the per-backend TTS voice + prosody automatically
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Sequence

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


# Fish consumes `[softly]` as prosody control — quiet delivery so the ack
# doesn't compete with the upcoming real answer.
_FISH_ACKS: tuple[str, ...] = (
    "[softly] mm",
    "[softly] mhm",
    "[softly] hm",
    "[softly] okay",
)
_PLAIN_ACKS: tuple[str, ...] = ("mm", "mhm", "hm", "okay")


class MicroAckInjector(FrameProcessor):
    """Emits a short ack if the pipeline hasn't produced audio within
    `trigger_ms` of UserStoppedSpeakingFrame. Cancels if the bot starts
    speaking within the window."""

    def __init__(
        self,
        *,
        tts_backend: str,
        trigger_ms: int = 500,
        min_interval_secs: float = 4.0,
        enabled: bool = True,
    ) -> None:
        super().__init__()
        self._phrases: Sequence[str] = _FISH_ACKS if tts_backend == "fish" else _PLAIN_ACKS
        self._trigger_s = trigger_ms / 1000.0
        self._min_interval = min_interval_secs
        self._enabled = enabled
        self._bot_speaking = False
        self._last_ack_at = 0.0
        self._timer: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._cancel_timer()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif isinstance(frame, UserStartedSpeakingFrame):
            # User is still talking — cancel any pending ack.
            self._cancel_timer()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._arm_timer()

        await self.push_frame(frame, direction)

    def _arm_timer(self) -> None:
        if not self._enabled:
            return
        now = time.monotonic()
        if self._bot_speaking:
            return
        if now - self._last_ack_at < self._min_interval:
            return
        self._cancel_timer()
        self._timer = asyncio.create_task(self._fire_after_delay())

    async def _fire_after_delay(self) -> None:
        try:
            await asyncio.sleep(self._trigger_s)
            if self._bot_speaking:
                return
            phrase = random.choice(self._phrases)
            self._last_ack_at = time.monotonic()
            logger.info(f"[micro-ack] {phrase!r}")
            await self.push_frame(
                TTSSpeakFrame(phrase, append_to_context=False),
                FrameDirection.DOWNSTREAM,
            )
        except asyncio.CancelledError:
            pass

    def _cancel_timer(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None
