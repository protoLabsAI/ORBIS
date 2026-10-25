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
cancels on the LLM's own *real output* (``LLMTextFrame`` /
``FunctionCallsStartedFrame`` / bot speech), which only flow downstream from
the LLM. Placed *before* the LLM it never sees those cancel frames and fires on
every turn.

``LLMFullResponseStartFrame`` is deliberately NOT a cancel signal: pipecat's
LLM service pushes it BEFORE the HTTP call (see ``llm_error_announcer``), so
cancelling on it disarmed the watchdog on every turn and a slow first token
left the user in silent "thinking". Second guard: a turn whose
``LLMFullResponseEndFrame`` arrives with no text / tool call / speech since the
user stopped (HTTP 200 but empty completion) gets a recovery line immediately
rather than waiting out the timer.
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
    LLMFullResponseEndFrame,
    LLMTextFrame,
    InterruptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .user_state import turn_cancelled_since

logger = logging.getLogger(__name__)

# Default silence (seconds after the user stops) before the recovery line.
DEFAULT_STALL_SECS = 8.0

# Canned recovery lines — varied so a repeat stall doesn't say the same
# thing. Real words (Kokoro-safe); Fish gets the [softly] prefix at emit.
_STALL_LINES: tuple[str, ...] = (
    "sorry, bit of a delay — give me a second",
    "hang on, this is taking a moment",
    "one moment, still working on that",
    "sorry, just a sec — almost there",
    "hmm, this one's being slow, bear with me",
)

# A dismiss ("cancel" / "stop listening") within this many seconds before a
# watchdog fire means the fire belongs to the dismissed interaction — stay
# quiet. The window is forward-looking from the cancel, so a phantom re-arm
# shortly after the dismiss (bot-audio bleed on the open full-duplex mic, a
# sub-threshold utterance) can't slip a recovery line into dead air.
_CANCEL_QUIET_GRACE = 8.0

# Spoken when the LLM finished a turn without producing anything.
_EMPTY_TURN_LINES: tuple[str, ...] = (
    "Sorry, I lost my train of thought — could you say that again?",
)


class StallWatchdog(FrameProcessor):
    """Speaks a recovery line if the agent goes silent after the user's turn."""

    def __init__(
        self,
        *,
        stall_secs: float = DEFAULT_STALL_SECS,
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
        self._turn_open = False  # a user turn ended and awaits its reply

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self._observe(frame)
        await self.push_frame(frame, direction)

    async def _observe(self, frame: Frame) -> None:
        if isinstance(frame, UserStoppedSpeakingFrame):
            self._responding = False
            self._turn_open = True
            self._arm()
        elif isinstance(frame, LLMFullResponseEndFrame):
            # Response finished. If nothing real came out of it for an open
            # user turn, the user would sit in "thinking" — say so now.
            if self._turn_open and not self._responding:
                self._turn_open = False
                self._cancel()
                await self._speak_empty_turn()
            else:
                self._turn_open = False
        elif isinstance(
            frame,
            (
                TTSSpeakFrame,
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
            self._turn_open = False
            self._cancel()

    async def _speak_empty_turn(self) -> None:
        if not self._enabled:
            return
        if turn_cancelled_since(time.monotonic() - _CANCEL_QUIET_GRACE):
            return
        line = random.choice(_EMPTY_TURN_LINES)
        if self._tts_backend == "fish":
            line = f"[softly] {line}"
        logger.warning(f"[stall] LLM turn ended with no output — recovery: {line!r}")
        await self.push_frame(
            TTSSpeakFrame(line, append_to_context=False), FrameDirection.DOWNSTREAM
        )

    def _arm(self) -> None:
        self._cancel()
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
            # A recent dismiss ("cancel" / "stop listening") means this fire
            # belongs to the dismissed interaction — stay quiet. Check a forward
            # window from the cancel, NOT the arm time: a phantom re-arm AFTER
            # the cancel (bot-audio bleed on the open full-duplex mic, a
            # sub-threshold utterance) slips past an arm-time check and speaks a
            # recovery line into dead air — the bug this guards.
            if turn_cancelled_since(
                time.monotonic() - self._stall_secs - _CANCEL_QUIET_GRACE
            ):
                return
            line = self._pick()
            logger.warning(
                f"[stall] no response in {self._stall_secs}s — recovery: {line!r}"
            )
            # The user is about to hear this line; a late empty completion on
            # the same turn must not speak a second one on top of it.
            self._responding = True
            await self.push_frame(
                TTSSpeakFrame(line, append_to_context=False),
                FrameDirection.DOWNSTREAM,
            )
        except asyncio.CancelledError:
            pass
