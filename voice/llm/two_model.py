"""Two-model LLM routing — smart model decides, fast model narrates.

Pipecat's tool-execution cycle already makes two separate completion
requests per delegated turn:

  - **call 1** — user message → *tool decision* (does the agent need to
    delegate / call a tool, and with what arguments?)
  - **call 2** — tool result in context → *voice narration* (turn the
    tool output into something to say)

Call 1 is where the real reasoning happens; call 2 is mechanical. So we
run call 1 on a stronger ``router_model`` (e.g. ``protolabs/smart``) and
call 2 on a cheaper/faster ``content_model`` (e.g. ``protolabs/fast``).

We detect call 2 by the presence of a ``role: tool`` message in the
context. Turns with no tool use never see a tool result, so they stay on
the router (smart) model — that single call both decides *and* answers,
so it gets the stronger model.

Unset router/content models → this service isn't constructed at all (the
factory builds a plain ``OpenAILLMService``), so the default single-model
path is unchanged.
"""

import logging

from pipecat.adapters.services.open_ai_adapter import OpenAILLMInvocationParams

from .guarded import GuardedOpenAILLMService

logger = logging.getLogger(__name__)


def _has_tool_result(messages) -> bool:
    """True if any context message is a tool result (``role: tool``).

    Messages may be plain dicts or pydantic message params; handle both.
    """
    for m in messages or []:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if role == "tool":
            return True
    return False


class TwoModelOpenAILLMService(GuardedOpenAILLMService):
    """OpenAI-compat LLM service that swaps the model per call: the
    ``router_model`` for tool-decision / plain turns, the
    ``content_model`` for post-tool narration turns.

    Inherits the tool-loop guard via ``GuardedOpenAILLMService`` — the
    ``super()`` call below is the guarded params build. A stalled turn still
    carries tool results in context, so it routes to the ``content_model``,
    which is the right one for the job: the guard's stop step asks for plain
    narration, not a decision.

    Only the ``model`` request param differs between the two — everything
    else (messages, tools, sampling, ``extra_body``) is identical, so the
    same gateway endpoint serves both. Both models must be reachable at
    the same ``base_url``.
    """

    def __init__(self, *, router_model: str, content_model: str, **kwargs):
        super().__init__(**kwargs)
        self._router_model = router_model
        self._content_model = content_model

    def build_chat_completion_params(
        self, params_from_context: OpenAILLMInvocationParams
    ) -> dict:
        params = super().build_chat_completion_params(params_from_context)
        if _has_tool_result(params.get("messages")):
            params["model"] = self._content_model
        else:
            params["model"] = self._router_model
        return params
