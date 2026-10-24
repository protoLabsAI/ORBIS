"""User-turn start strategies that stop noise from interrupting the bot.

Pipecat's default start strategies are ``[VAD, Transcription]``: ANY VAD
speech-start (a cough, a chair, bleed from the bot's own tail) opens a user
turn, and an opened turn broadcasts an ``InterruptionFrame`` that cancels the
LLM stream / TTS — even when the bot is only *thinking*. The symptom is a
spoken line cut off ~1s in with no transcript behind it.

Instead:

- bot idle  -> VAD starts the turn immediately (unchanged latency);
- bot busy (speaking, or a response is pending after the user's last turn)
  -> a bare VAD blip is ignored and only real words interrupt, via pipecat's
  ``MinWordsUserTurnStartStrategy`` (``min_words`` while the bot is audibly
  speaking, one word otherwise).
"""

from __future__ import annotations

import os
import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import (
    BaseUserTurnStartStrategy,
    MinWordsUserTurnStartStrategy,
    VADUserTurnStartStrategy,
)

# After a user turn ends we expect a response; if the bot never speaks (a
# silent tool call, a swallowed turn) don't stay "busy" forever.
_PENDING_RESPONSE_TTL_S = 10.0


def barge_in_min_words() -> int:
    """Words required to interrupt a speaking bot (0 disables gating)."""
    try:
        return max(int(os.environ.get("BARGE_IN_MIN_WORDS", "2")), 0)
    except ValueError:
        return 2


class BotAwareVADUserTurnStartStrategy(VADUserTurnStartStrategy):
    """VAD start that defers to transcripts while the bot is busy."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._bot_speaking = False
        self._response_pending_until = 0.0

    @property
    def bot_busy(self) -> bool:
        return self._bot_speaking or time.monotonic() < self._response_pending_until

    async def handle_user_turn_stopped(self):
        self._response_pending_until = time.monotonic() + _PENDING_RESPONSE_TTL_S

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._response_pending_until = 0.0
        elif self.bot_busy:
            return ProcessFrameResult.CONTINUE
        return await super().process_frame(frame)


def build_start_strategies(
    min_words: int | None = None,
) -> list[BaseUserTurnStartStrategy] | None:
    """Start strategies gated on real words; None keeps pipecat's defaults."""
    n = barge_in_min_words() if min_words is None else min_words
    if n <= 0:
        return None
    return [
        BotAwareVADUserTurnStartStrategy(),
        MinWordsUserTurnStartStrategy(min_words=n),
    ]
