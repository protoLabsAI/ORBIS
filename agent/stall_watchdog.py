"""Stall watchdog (orbis-1sb / E2).

A frozen LLM or TTS leaves the user in **dead air** — far worse in voice
than in a CLI, where a spinner at least shows life. This watchdog arms when
the user stops speaking and, if the agent produces *no sign of working*
(no LLM text, no tool call, no bot audio) within ``stall_secs``, speaks one
brief recovery line ("sorry — bit of a delay, give me a second") so the
silence is acknowledged instead of hanging.

It is deliberately coarse: a long threshold (the micro-ack already covers
the normal ~2.5s "thinking" gap) and a single fire per turn, re-armed only
on the next ``UserStoppedSpeakingFrame``. The recovery line is canned (no
LLM call) — the whole point is that the LLM may be the thing that's stuck.

**Placement matters: this MUST sit downstream of the LLM service.** It arms
on ``UserStoppedSpeakingFrame`` (which flows downstream to any position) and
cancels on the LLM's own output (``LLMFullResponseStartFrame`` /
``LLMTextFrame`` / ``FunctionCallsStartedFrame``), which only flow downstream
from the LLM. Placed *before* the LLM it never sees those cancel frames and
fires on every turn.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    FunctionCallsStartedFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    InterruptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .user_state import turn_cancelled_since

logger = logging.getLogger(__name__)

# Canned recovery lines — varied so a repeat stall doesn't say the same
# thing. Real words (Kokoro-safe); Fish gets the [softly] prefix at emit.
_STALL_LINES: tuple[str, ...] = (
    "sorry, bit of a delay — give me a second",
    "hang on, this is taking a moment",
    "one moment, still working on that",
    "sorry, just a sec — almost there",
    "hmm, this one's being slow, bear with me",
)


class StallWatchdog(FrameProcessor):
    """Speaks a recovery line if the agent goes silent after the user's turn."""

    def __init__(
        self,
        *,
        stall_secs: float = 8.0,
        enabled: bool = True,
        tts_backend: str = "kokoro",
    ) -> None:
        super().__init__()
        self._stall_secs = stall_secs
        self._enabled = enabled
        self._tts_backend = tts_backend
        self._timer: asyncio.Task | None = None
        self._responding = False  # any sign of work since the user stopped
        self._last_line: str | None = None
        self._armed_at = 0.0  # monotonic time of the last arm (cancel check)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStoppedSpeakingFrame):
            self._responding = False
            self._arm()
        elif isinstance(
            frame,
            (
                LLMFullResponseStartFrame,
                LLMTextFrame,
                BotStartedSpeakingFrame,
                FunctionCallsStartedFrame,
            ),
        ):
            # The agent is clearly working — cancel the watchdog this turn.
            # Placed after the LLM, these all flow downstream to here.
            self._responding = True
            self._cancel()
        elif isinstance(frame, (UserStartedSpeakingFrame, InterruptionFrame)):
            # Next turn started, or the turn was aborted (a "cancel" / "stop
            # listening" swallowed upstream by CancelGate) — stand down so we
            # don't speak a recovery line into an intentionally-empty turn.
            self._cancel()

        await self.push_frame(frame, direction)

    def _arm(self) -> None:
        self._cancel()
        self._armed_at = time.monotonic()
        if self._enabled and self._stall_secs > 0:
            self._timer = asyncio.create_task(self._fire_after_delay())

    def _cancel(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    def _pick(self) -> str:
        line = random.choice(_STALL_LINES)
        if line == self._last_line and len(_STALL_LINES) > 1:
            line = random.choice([x for x in _STALL_LINES if x != self._last_line])
        self._last_line = line
        if self._tts_backend == "fish":
            line = f"[softly] {line}"
        return line

    async def _fire_after_delay(self) -> None:
        try:
            await asyncio.sleep(self._stall_secs)
            if self._responding:
                return
            if turn_cancelled_since(self._armed_at):
                # The user dismissed this turn ("cancel" / "stop listening") —
                # it's intentionally empty, not a stall. Stay quiet.
                return
            line = self._pick()
            logger.warning(
                f"[stall] no response in {self._stall_secs}s — recovery: {line!r}"
            )
            await self.push_frame(
                TTSSpeakFrame(line, append_to_context=False),
                FrameDirection.DOWNSTREAM,
            )
        except asyncio.CancelledError:
            pass
