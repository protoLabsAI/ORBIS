"""AskGate — routes a user transcript to a paused HITL orchestration run.

When a background ``orchestrate(goal)`` run calls ``ask_user`` and is waiting on
the user's answer, ORBIS parks a ``PendingAsk`` on the live session
(``agent/user_state.py``). This processor sits right after STT/audio-tags and
before the context aggregator: on the next user ``TranscriptionFrame`` it
resolves the run's future with the text and **swallows that frame**, so the
answer feeds the paused run instead of starting a fresh LLM turn.

With no pending ask, every frame passes through untouched — so normal turns are
completely unaffected.
"""

from __future__ import annotations

import logging

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent.user_state import take_pending_ask

logger = logging.getLogger(__name__)


class AskGate(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if (
            isinstance(frame, TranscriptionFrame)
            and direction == FrameDirection.DOWNSTREAM
        ):
            text = (frame.text or "").strip()
            if text:
                pending = take_pending_ask()
                if pending is not None and not pending.future.done():
                    logger.info(
                        "[ask-gate] answering paused orchestration: %.60r", text
                    )
                    pending.future.set_result(text)
                    return  # swallow — do not start a normal LLM turn

        await self.push_frame(frame, direction)
