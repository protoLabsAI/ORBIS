"""BackchannelController — emits brief listener-acks during long user turns.

Backchannels ("mm-hmm", "yeah", "right") are different from fillers:

  - **Filler** fires AFTER the user stops speaking, while the agent does
    a tool call.
  - **Backchannel** fires WHILE the user is speaking, signalling "I'm
    tracking, keep going."

Implementation: watch UserStartedSpeakingFrame / UserStoppedSpeakingFrame.
On started, kick off a timer task. After `first_after_secs`, emit a
backchannel via the FillerGenerator. Then continue every
`interval_secs` until the user stops. Cancel + drain on UserStopped.

Race defenses (all load-bearing — removing any one reintroduces stale
"mm-hmm" at the end of the agent's reply):

  1. Leading indicator — `_bot_thinking` flips true on BotLlmStarted
     (before audio begins), earlier than `_bot_speaking`. Cancels the
     in-flight loop the moment the agent starts generating.
  2. Pre-commit grace — after the generator resolves, sleep
     `COMMIT_GRACE_MS` and re-check state. Catches the case where
     user-stop + llm-start land between the state check and the emit.
  3. In-flight drop — every TTSSpeakFrame we emit is tagged. When it
     re-enters the pipeline (task.queue_frame injects at the top), we
     re-evaluate state at our processor and drop instead of pushing
     downstream if the world has moved on.

This is intentionally minimal — most user turns are <5s and never
trigger a backchannel at all. The defaults err quiet.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .filler import FillerGenerator, Verbosity

logger = logging.getLogger(__name__)

FIRST_AFTER_SECS = float(os.environ.get("BACKCHANNEL_FIRST_SECS", "5.0"))
INTERVAL_SECS = float(os.environ.get("BACKCHANNEL_INTERVAL_SECS", "6.0"))
# Delay between "generator has our phrase" and "we queue the frame".
# Gives the pipeline a tick to fire BotLlmStarted / UserStopped so the
# re-check catches them. ~180ms is below perceptual threshold for
# ambient listener-acks.
COMMIT_GRACE_MS = int(os.environ.get("BACKCHANNEL_COMMIT_GRACE_MS", "180"))

FrameEmitter = Callable[[Frame], Awaitable[None]]

# Private attribute used to tag our own TTSSpeakFrames so we can
# identify + drop them if they re-enter us after state changed.
_BACKCHANNEL_TAG = "_pv_is_backchannel"


class BackchannelController(FrameProcessor):
    def __init__(
        self,
        *,
        generator: FillerGenerator,
        tts_backend: str,
        enabled: bool = True,
        first_after_secs: float | None = None,
        interval_secs: float | None = None,
    ):
        super().__init__()
        self._gen = generator
        self._backend = tts_backend
        self._enabled = enabled
        self._first_after_secs = (
            first_after_secs if first_after_secs is not None else FIRST_AFTER_SECS
        )
        self._interval_secs = (
            interval_secs if interval_secs is not None else INTERVAL_SECS
        )
        self._loop_task: asyncio.Task | None = None
        self._emitter: FrameEmitter | None = None
        self._bot_speaking = False
        self._bot_thinking = False  # BotLlm{Started,Stopped} — leading indicator
        self._user_speaking = False

    def set_emitter(self, emitter: FrameEmitter) -> None:
        """Wired by app.py post-construction (task.queue_frame)."""
        self._emitter = emitter

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking = True
            self._start_loop()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
            self._cancel_loop()
        elif isinstance(frame, LLMFullResponseStartFrame):
            # Leading indicator — agent is about to speak. Fires earlier
            # than BotStartedSpeaking (which waits for audio).
            self._bot_thinking = True
            self._cancel_loop()
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._bot_thinking = False
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._cancel_loop()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False

        # In-flight drop: if this is one of OUR backchannel frames passing
        # back through us (task.queue_frame re-enters at the pipeline top),
        # refuse to push it downstream when the world has moved on. Without
        # this the frame stacks in the TTS queue and plays after the agent.
        if (
            isinstance(frame, TTSSpeakFrame)
            and getattr(frame, _BACKCHANNEL_TAG, False)
            and self._should_drop()
        ):
            logger.info("[backchannel] dropping in-flight frame; state changed after emit")
            return

        await self.push_frame(frame, direction)

    def _should_drop(self) -> bool:
        """Any of these means a backchannel is no longer appropriate."""
        return self._bot_thinking or self._bot_speaking or not self._user_speaking

    def _start_loop(self) -> None:
        if not self._enabled:
            return
        if self._gen.settings.verbosity is Verbosity.SILENT:
            return
        # Don't start while the agent is already talking — a spurious
        # UserStartedSpeakingFrame mid-bot-TTS is almost always echo bleed.
        if self._bot_thinking or self._bot_speaking:
            return
        if self._loop_task and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._loop())

    def _cancel_loop(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        self._loop_task = None

    async def _loop(self) -> None:
        try:
            await asyncio.sleep(self._first_after_secs)
            await self._emit_one()
            while True:
                await asyncio.sleep(self._interval_secs)
                await self._emit_one()
        except asyncio.CancelledError:
            pass

    async def _emit_one(self) -> None:
        from agent import tracing
        with tracing.span("backchannel.emit") as sp:
            try:
                phrase = await self._gen.backchannel(tts_backend=self._backend)
            except Exception as e:
                sp.update(level="WARNING", status_message=str(e))
                logger.warning(f"[backchannel] generator raised: {e}")
                return
            if not phrase:
                sp.update(status_message="skipped (empty)")
                return
            # First check — after the generator call but before the grace.
            if self._should_drop():
                sp.update(status_message="skipped (state changed during generate)")
                return
            # Grace window. Lets BotLlmStarted / UserStopped land in the
            # pipeline before we commit. Without this the race between
            # "generator resolved" and "agent just started responding" can
            # still slip a backchannel into the TTS queue behind the reply.
            await asyncio.sleep(COMMIT_GRACE_MS / 1000.0)
            if self._should_drop():
                sp.update(status_message="skipped (state changed during grace)")
                return

            sp.update(output=phrase)
            logger.info(f"[backchannel] {phrase!r}")
            # Backchannels MUST stay out of LLM context — they're listener
            # noises, not assistant turns.
            frame = TTSSpeakFrame(phrase, append_to_context=False)
            # Tag so the in-flight drop in process_frame can identify this
            # as one of ours when the queue_frame injection re-enters us.
            setattr(frame, _BACKCHANNEL_TAG, True)
            if self._emitter is not None:
                await self._emitter(frame)
            else:
                await self.push_frame(frame)
