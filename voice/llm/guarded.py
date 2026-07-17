"""OpenAI-compat LLM service with the tool-loop guard applied.

``BaseOpenAILLMService.get_chat_completions`` builds its request through
``build_chat_completion_params``, so overriding that one method is enough to
guard every request on the OpenAI path — including the tool-result re-triggers
that pipecat fires on its own, which is exactly where a loop lives.

The Ollama and MLX adapters do *not* route through here: they override
``get_chat_completions`` outright and never call
``build_chat_completion_params``, so they call ``apply_tool_loop_guard``
themselves. ``tests/test_tool_loop.py`` pins that all four backends do.

See ``agent/tool_loop.py`` for the policy.
"""

from __future__ import annotations

from pipecat.adapters.services.open_ai_adapter import OpenAILLMInvocationParams
from pipecat.services.openai.llm import OpenAILLMService

from agent.tool_loop import apply_tool_loop_guard


class GuardedOpenAILLMService(OpenAILLMService):
    """``OpenAILLMService`` + the tool-loop guard. Behaviourally identical on
    any healthy turn; breaks a no-progress repeated tool call."""

    def build_chat_completion_params(
        self, params_from_context: OpenAILLMInvocationParams
    ) -> dict:
        return apply_tool_loop_guard(super().build_chat_completion_params(params_from_context))
