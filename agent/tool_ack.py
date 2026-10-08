"""Keep a tool's opening acknowledgement from repeating speech already sent."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


@dataclass
class ToolOpeningAckFrame(TTSSpeakFrame):
    """An optional opening ack tied to the user turn that requested it."""

    turn_id: int = 0


class ToolAckGate(FrameProcessor):
    """Let existing speech acknowledge the turn; drop redundant opening acks.

    Runs after reasoning filtering and before the speech logger/TTS. The LLM
    can still emit a preamble despite the prompt, and queued acks can arrive
    after a result or interruption. Check at consumption, not only emission.
    Responses stay streamed; progress updates and explicit deliveries pass.
    """

    def __init__(self) -> None:
        super().__init__()
        self._turn_id = 0
        self._spoken = False

    def opening_frame(self, text: str) -> ToolOpeningAckFrame:
        return ToolOpeningAckFrame(
            text=text, append_to_context=False, turn_id=self._turn_id,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, (UserStartedSpeakingFrame, InterruptionFrame)):
                self._turn_id += 1
                self._spoken = False
            elif isinstance(frame, ToolOpeningAckFrame):
                if frame.turn_id != self._turn_id or self._spoken:
                    logger.info(
                        "[filler:opening] suppressed: %s",
                        "stale turn" if frame.turn_id != self._turn_id
                        else "speech already started this turn",
                    )
                    return
            if isinstance(frame, (LLMTextFrame, TTSSpeakFrame)) and frame.text.strip():
                self._spoken = True
        await self.push_frame(frame, direction)
