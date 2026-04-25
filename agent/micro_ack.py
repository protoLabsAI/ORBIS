"""Acoustic micro-ack injector.

Vapi's "Fill Injection" pattern. When the user stops speaking, start a
short timer. If the main pipeline (STT → LLM → TTS) hasn't produced
audio by the time the timer expires, emit a tiny acknowledgement (`mm`,
`mhm`, `hm`, `okay`). Gives the user a sense of "I heard you" without
waiting for the full response.

The threshold defaults to 1500 ms — slow enough that a fast local LLM
(e.g. gemma3n:e2b on M1, ~1 s end-to-end) cancels the timer before it
fires, but fast enough to bridge the silence on tool calls, large
prompts, or cold-starts. The original tuning was 500 ms, optimized
for cloud-LLM latency profiles; on local hardware that fired so
aggressively the ack played on every turn. The
``persona.behavior.micro_ack.first_ms`` config key (read by app.py
when constructing ``FillerSettings``) overrides the default per
persona.

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
from collections.abc import Callable, Sequence

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .filler import Verbosity

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
        # 1500ms gives a fast local LLM (e.g. gemma3n:e2b on M1, ~1s
        # round-trip) the chance to start speaking before a filler
        # fires. The original 500ms default was tuned for the slow-LLM
        # era; with the Ollama-native adapter + small models, the
        # filler always won that race and surfaced as "the bot says
        # 'mm' before every reply", which felt twitchy. The persona-
        # config `behavior.micro_ack.first_ms` still overrides this
        # default per-skill if a particular agent wants snappier acks.
        trigger_ms: int = 1500,
        min_interval_secs: float = 4.0,
        enabled: bool = True,
        # Callable returning the live verbosity. When SILENT, suppress
        # emission. Live (not snapshot) so /api/verbosity flips take
        # effect on the next ack without needing a session reconnect.
        # None = no gate (back-compat for tests + callers that don't
        # have a UserState handy).
        verbosity_getter: Callable[[], Verbosity] | None = None,
    ) -> None:
        super().__init__()
        self._phrases: Sequence[str] = _FISH_ACKS if tts_backend == "fish" else _PLAIN_ACKS
        self._trigger_s = trigger_ms / 1000.0
        self._min_interval = min_interval_secs
        self._enabled = enabled
        self._verbosity_getter = verbosity_getter
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
        from agent import tracing
        try:
            await asyncio.sleep(self._trigger_s)
            if self._bot_speaking:
                return
            # Live verbosity check: a silent persona shouldn't emit
            # acoustic acks. Checked here rather than at _arm_timer so
            # a runtime /api/verbosity flip during the trigger window
            # is honored on the same turn.
            #
            # Wrapped in try/except: the getter is caller-provided; if
            # it raises (e.g. user_state torn down mid-shutdown), the
            # background task would otherwise fail with
            # "Task exception was never retrieved" and silently skip
            # the ack. Treat the failure as non-SILENT and continue —
            # better to over-emit one filler than to crash the timer.
            if self._verbosity_getter is not None:
                try:
                    verbosity = self._verbosity_getter()
                except Exception as e:
                    logger.warning(f"[micro-ack] verbosity_getter raised: {e}")
                    verbosity = None
                if verbosity is Verbosity.SILENT:
                    return
            phrase = random.choice(self._phrases)
            self._last_ack_at = time.monotonic()
            with tracing.span("filler.micro_ack") as sp:
                sp.update(output=phrase)
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
