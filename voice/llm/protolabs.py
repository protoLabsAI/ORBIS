"""ProtoLabs LLM adapter.

Thin subclass of ``OpenAILLMService`` that handles the ProtoLabs gateway
returning ``delta.content = None`` with the actual text in
``delta.reasoning_content`` (a thinking-model artefact where the gateway
leaks the reasoning field instead of the content field).

We override ``get_chat_completions`` to transparently remap
``reasoning_content → content`` on every chunk before the base class
processes it. No other behaviour changes.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from pipecat.services.openai.llm import OpenAILLMService

logger = logging.getLogger(__name__)


class ProtoLabsLLMService(OpenAILLMService):
    """OpenAI-compat adapter for api.proto-labs.ai.

    The gateway currently serves a thinking model whose streaming chunks
    have ``delta.content = None`` and text in ``delta.reasoning_content``.
    This adapter remaps that so Pipecat's base processor sees a normal
    ``delta.content`` token stream.
    """

    async def get_chat_completions(
        self,
        context: Any,
        messages: list[dict],
    ) -> AsyncGenerator:
        stream = await super().get_chat_completions(context, messages)
        return self._remap_reasoning(stream)

    async def _remap_reasoning(self, stream: AsyncGenerator) -> AsyncGenerator:
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta
                if delta.content is None:
                    rc = getattr(delta, "reasoning_content", None)
                    if rc:
                        # Patch in-place so the base class sees normal content.
                        object.__setattr__(delta, "content", rc)
            except (AttributeError, IndexError):
                pass
            yield chunk
