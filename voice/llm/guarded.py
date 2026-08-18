"""OpenAI-compat LLM service with the tool-loop guard + dead-turn recovery.

``BaseOpenAILLMService.get_chat_completions`` builds its request through
``build_chat_completion_params``, so overriding that one method is enough to
guard every request on the OpenAI path — including the tool-result re-triggers
that pipecat fires on its own, which is exactly where a loop lives.

The Ollama and MLX adapters do *not* route through here: they override
``get_chat_completions`` outright and never call
``build_chat_completion_params``, so they call ``apply_tool_loop_guard``
themselves. ``tests/test_tool_loop.py`` pins that all four backends do.

See ``agent/tool_loop.py`` for the policy.

Dead-turn recovery (#694): a gateway stream flake can truncate a tool call
mid-JSON (live-QA 2026-08-18: ``arguments: {"`` then EOF). Pipecat's base
service drops it with a parse warning and the completion finishes having
pushed NEITHER text NOR a function call — a silent turn. The stall watchdog
can't catch it (LLM frames did stream, so it disarmed). A reasoning model
that burns its whole budget in ``reasoning_content`` produces the same
nothing. So the service itself tracks whether a completion emitted any
output, and speaks one short canned recovery line when it didn't — the user
hears "that didn't land, go again" instead of silence.
"""

from __future__ import annotations

import logging

from pipecat.adapters.services.open_ai_adapter import OpenAILLMInvocationParams
from pipecat.frames.frames import (
    Frame,
    FunctionCallInProgressFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.openai.llm import OpenAILLMService

from agent.tool_loop import apply_tool_loop_guard

logger = logging.getLogger(__name__)

# Canned + append_to_context=False: the recovery must be instant, must not
# depend on the (possibly broken) LLM, and must not teach the model to riff
# on its own failure line.
_RECOVERY_LINE = "Sorry — that one got garbled on my end. Say it again?"


class GuardedOpenAILLMService(OpenAILLMService):
    """``OpenAILLMService`` + the tool-loop guard + dead-turn recovery.
    Behaviourally identical on any healthy turn."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._turn_had_output = False

    def build_chat_completion_params(
        self, params_from_context: OpenAILLMInvocationParams
    ) -> dict:
        return apply_tool_loop_guard(super().build_chat_completion_params(params_from_context))

    async def push_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        if isinstance(frame, FunctionCallInProgressFrame) or (
            isinstance(frame, LLMTextFrame) and frame.text
        ):
            self._turn_had_output = True
        await super().push_frame(frame, direction)

    async def _process_context(self, context):
        self._turn_had_output = False
        await super()._process_context(context)
        # A cancelled/errored completion raises out of super() — this only
        # runs on a completion that finished "cleanly" yet said nothing.
        if not self._turn_had_output:
            logger.warning(
                "[llm-guard] completion produced neither text nor a tool call "
                "(truncated tool-call stream? reasoning-only response?) — "
                "speaking recovery line"
            )
            await self.push_frame(
                TTSSpeakFrame(_RECOVERY_LINE, append_to_context=False)
            )
