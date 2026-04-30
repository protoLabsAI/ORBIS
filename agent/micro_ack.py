"""Acoustic micro-ack injector.

Vapi's "Fill Injection" pattern. When the user stops speaking, start a
short timer. If the main pipeline (STT → LLM → TTS) hasn't produced
audio by the time the timer expires, emit a tiny acknowledgement (`mm`,
`mhm`, `hm`, `okay`). Gives the user a sense of "I heard you" without
waiting for the full response.

Pipeline placement
------------------
This processor MUST be inserted **between the LLM service and the TTS
service**:

    ... user_agg → BargeInGate → backchannel → delivery →
        llm → MicroAckInjector → tts → transport.output()

Earlier placement (before the LLM) led to a subtle race that surfaced
as "the ack plays AFTER the bot's reply finished":

  1. User stops talking.
  2. LLM starts streaming TextFrames downstream toward TTS.
  3. 1500 ms later the timer fires and pushes ``TTSSpeakFrame``
     downstream.
  4. The TTSSpeakFrame arrives at TTS *behind* the LLM's TextFrames in
     pipeline order. Pipecat's TTS service plays them in arrival
     order, so the ack queues up after the bot's reply.

With the processor sitting between LLM and TTS, ``LLMFullResponseStartFrame``
flows past it the moment the LLM begins generating — we cancel the
timer immediately, so the ack only ever fires when the LLM has *not*
started, which means TTS has nothing queued and the ack plays first.

This mirrors Pipecat's own ``UserIdleController`` design: a timer that
arms on a known event and cancels on every signal that "the system is
no longer idle" (BotStartedSpeaking, FunctionCallsStarted, LLM start).

The threshold defaults to 1500 ms — slow enough that a fast local LLM
(e.g. gemma3n:e2b on M1, ~1 s end-to-end) cancels the timer before it
fires, but fast enough to bridge the silence on tool calls, large
prompts, or cold-starts. The
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
    FunctionCallCancelFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
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
# Kokoro's neural TTS mangles unvoiced interjections ("mm", "hm", "mhm")
# — they fall outside its training distribution and come out stilted or
# robotic. Real short words render cleanly, so the kokoro pool is
# limited to natural acknowledgements.
_KOKORO_ACKS: tuple[str, ...] = ("yeah", "got it", "right", "okay")
_PLAIN_ACKS: tuple[str, ...] = ("mm", "mhm", "hm", "okay")


def _phrases_for_backend(tts_backend: str) -> tuple[str, ...]:
    if tts_backend == "fish":
        return _FISH_ACKS
    if tts_backend == "kokoro":
        return _KOKORO_ACKS
    return _PLAIN_ACKS


# Grace window after the bot finishes speaking. The bot's own audio
# leaking back through (echo, mic-self-reception, residual VAD trail)
# can briefly retrigger UserStarted→UserStopped; without this gate the
# timer re-arms and fires an ack into the silence at end-of-turn.
_POST_BOT_GRACE_S: float = 1.5


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
        self._phrases: Sequence[str] = _phrases_for_backend(tts_backend)
        self._trigger_s = trigger_ms / 1000.0
        self._min_interval = min_interval_secs
        self._enabled = enabled
        self._verbosity_getter = verbosity_getter
        self._bot_speaking = False
        self._last_ack_at = 0.0
        # Suppress arming until this monotonic clock value — extended
        # whenever the bot stops speaking. See _POST_BOT_GRACE_S.
        self._post_bot_grace_until = 0.0
        # True while the LLM is actively streaming a response (between
        # LLMFullResponseStartFrame and LLMFullResponseEndFrame). Acks
        # are suppressed in this window because TTS already has the
        # bot's reply queued and would play the ack afterwards.
        self._llm_responding = False
        # Counter for in-flight function calls — incremented on
        # FunctionCallsStartedFrame, decremented on result/cancel.
        # While > 0 the LLM is waiting on a tool, the ack is *welcome*
        # there (long silence to fill), but a fresh
        # UserStoppedSpeakingFrame in that window shouldn't re-arm if
        # we already played one for this turn — handled via
        # _last_ack_at / _min_interval.
        self._function_calls_in_progress = 0
        self._timer: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            # Upstream from transport.output once real audio reaches the
            # client — definitely too late for an ack.
            logger.debug("[micro-ack] BotStarted (cancel timer)")
            self._bot_speaking = True
            self._cancel_timer()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            logger.debug("[micro-ack] BotStopped (post-bot grace armed)")
            self._bot_speaking = False
            self._post_bot_grace_until = time.monotonic() + _POST_BOT_GRACE_S
        elif isinstance(frame, LLMTextFrame):
            # The first actual generated token. Use this — NOT
            # LLMFullResponseStartFrame — as the cancel signal:
            # LLMFullResponseStartFrame fires at prompt-receipt
            # (~2 ms after UserStopped), long before any tokens exist,
            # which would suppress every legitimate ack. LLMTextFrame
            # is "real text now flowing toward TTS".
            if not self._llm_responding:
                logger.debug("[micro-ack] first LLMTextFrame (cancel timer)")
                self._llm_responding = True
                self._cancel_timer()
        elif isinstance(frame, LLMFullResponseEndFrame):
            logger.debug("[micro-ack] LLMFullResponseEnd")
            self._llm_responding = False
        elif isinstance(frame, FunctionCallsStartedFrame):
            # Tool call started — bot won't speak until the call
            # returns. The current arm cycle is fine to fire (long
            # silence ahead), but we track depth so we can decide later
            # to suppress repeats.
            self._function_calls_in_progress += len(frame.function_calls)
        elif isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
            self._function_calls_in_progress = max(
                0, self._function_calls_in_progress - 1
            )
        elif isinstance(frame, UserStartedSpeakingFrame):
            # User is still talking — cancel any pending ack.
            logger.debug("[micro-ack] UserStarted (cancel timer)")
            self._cancel_timer()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            logger.debug(
                "[micro-ack] UserStopped (arm bot=%s llm=%s grace=%.1f)",
                self._bot_speaking,
                self._llm_responding,
                max(0.0, self._post_bot_grace_until - time.monotonic()),
            )
            self._arm_timer()

        await self.push_frame(frame, direction)

    def _arm_timer(self) -> None:
        if not self._enabled:
            return
        now = time.monotonic()
        if self._bot_speaking:
            return
        if self._llm_responding:
            # LLM is mid-stream; whatever we push lands behind its
            # TextFrames in TTS.
            return
        if now < self._post_bot_grace_until:
            # End-of-turn audio bleed re-tripped VAD; don't ack.
            return
        if now - self._last_ack_at < self._min_interval:
            return
        self._cancel_timer()
        self._timer = asyncio.create_task(self._fire_after_delay())

    async def _fire_after_delay(self) -> None:
        from agent import tracing
        try:
            await asyncio.sleep(self._trigger_s)
            if self._bot_speaking or self._llm_responding:
                # Lost the race after sleep but before fire — the LLM
                # or transport announced output during our wait.
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
