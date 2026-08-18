"""AskGate — routes a user transcript to whoever is waiting on an answer.

Two kinds of parked question, checked in order:

1. **Orchestrate HITL** (``PendingAsk``): a background ``orchestrate(goal)``
   run called ``ask_user`` and is blocked on a future. The next transcript
   resolves it. (Single-slot; retired with orchestrate in #678 Phase D.)
2. **Delegate input-required** (``DelegateAsk``, #681): an A2A task (hub /
   fleet) parked on ``input-required`` — the orb spoke its question, and the
   next transcript is sent INTO THAT TASK via ``answer_delegate_ask``.
   Keyed by task id, oldest-first, TTL-guarded so a stale question can't
   swallow an unrelated utterance minutes later.

This processor sits right after STT/audio-tags (and after CancelGate, so a
"never mind" dismisses rather than answers) and before the context
aggregator: a routed answer is swallowed and never starts a fresh LLM turn.
With nothing parked, every frame passes through untouched.
"""

from __future__ import annotations

import asyncio
import logging

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent.user_state import take_oldest_delegate_ask, take_pending_ask

logger = logging.getLogger(__name__)


class AskGate(FrameProcessor):
    def __init__(self, registry=None, **kwargs):
        """``registry`` is the DelegateRegistry, needed to answer delegate
        asks (resolving the delegate's client). Without one, delegate-ask
        routing is disabled and only orchestrate HITL is handled."""
        super().__init__(**kwargs)
        self._registry = registry

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

                if self._registry is not None:
                    ask = take_oldest_delegate_ask()
                    if ask is not None:
                        logger.info(
                            "[ask-gate] answering %s task %s: %.60r",
                            ask.delegate, ask.task_id, text,
                        )
                        from agent.delegate_ask import answer_delegate_ask

                        asyncio.create_task(
                            answer_delegate_ask(ask, text, self._registry)
                        )
                        return  # swallow — the answer feeds the task

        await self.push_frame(frame, direction)
