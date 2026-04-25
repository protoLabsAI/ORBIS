"""Ollama-native LLM adapter for Pipecat.

Why this exists
---------------
Pipecat's stock ``OpenAILLMService`` talks to OpenAI-compatible
``/v1/chat/completions`` endpoints. Ollama exposes such an endpoint
but its OpenAI-compat shim ignores Ollama-specific request fields —
most importantly ``think: false``. As of Ollama 0.6+ several stock
models (gemma3/gemma4, qwen3, deepseek-r1) emit a ``reasoning``
delta stream before any user-visible ``content``. Pipecat's
sentence aggregator only chunks ``content``, so TTS waits for
sentence-break tokens that never arrive until the reasoning phase
finally ends — manifests as multi-second perceived latency between
the end of user speech and the bot's first audible reply.

Ollama's native ``/api/chat`` endpoint *does* honor ``think``. By
calling it directly and translating the streamed NDJSON into the
OpenAI ``ChatCompletionChunk`` shape pipecat expects, we get:

  - ``think: false`` actually disables reasoning preamble
  - First content tokens stream in 100-300ms instead of 6-8s
  - The rest of pipecat's pipeline (sentence aggregation, tool
    calls, interrupts, observers) keeps working unchanged

The translation is a thin shim — Ollama's chunk shape
``{"message": {"role": ..., "content": ...}, "done": false}`` maps
cleanly onto ``{"choices": [{"delta": {"role": ..., "content": ...},
"finish_reason": null}]}`` via ``types.SimpleNamespace`` ducks.
We don't try to reproduce the full openai SDK ChatCompletionChunk
class — pipecat reads only a small handful of fields, and shimming
those is enough.

What's NOT supported (yet)
--------------------------
- Tool / function calling. Ollama's native API supports it
  (``tools`` request param, ``message.tool_calls`` in response) but
  the pipecat tool-call format requires a more careful translation —
  out of scope for the first version. The adapter logs a warning
  and falls through with content-only when tools are present in
  the context.
- Vision / multimodal. Ollama supports image inputs on multimodal
  models; we ignore them for now (voice is text-in, text-out).
- ``stream_options.include_usage``. Ollama provides usage in the
  final ``done`` chunk natively; we surface it on the closing chunk.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
from pipecat.services.openai.base_llm import BaseOpenAILLMService

logger = logging.getLogger(__name__)


class OllamaLLMService(BaseOpenAILLMService):
    """Ollama adapter — uses ``/api/chat`` with ``think: false`` and
    streams the response back through pipecat as if it were an
    OpenAI ChatCompletion stream.

    Drop-in replacement for ``OpenAILLMService`` when the configured
    URL points at an Ollama instance. The ``base_url`` argument is
    expected in OpenAI-compat form (i.e. ending in ``/v1``); we
    strip the suffix to derive the Ollama root for ``/api/chat``.
    """

    def __init__(
        self,
        *,
        api_key: str = "ollama",
        base_url: str,
        model: str,
        think: bool = False,
        request_timeout: float = 120.0,
        **kwargs: Any,
    ):
        # The parent constructs an `openai.AsyncOpenAI` client we
        # never actually use (we override `get_chat_completions`),
        # but it's needed to satisfy `BaseOpenAILLMService.__init__`.
        # Pass an opaque api_key so the SDK doesn't error on missing
        # auth at construction time.
        super().__init__(
            api_key=api_key or "ollama",
            base_url=base_url,
            model=model,
            **kwargs,
        )
        self._think = think
        self._ollama_root = self._derive_root(base_url)
        # Separate httpx client for the native API. AsyncClient is
        # safe to reuse across requests + handles connection pooling.
        self._http = httpx.AsyncClient(timeout=request_timeout)

    @staticmethod
    def _derive_root(base_url: str) -> str:
        """``http://host:11434/v1`` → ``http://host:11434``. Works for
        any URL ending in ``/v1`` or ``/v1/``."""
        url = (base_url or "").rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        return url

    async def get_chat_completions(self, context):  # type: ignore[override]
        """Mirror of ``BaseOpenAILLMService.get_chat_completions`` that
        returns an async-iterable of OpenAI-shaped chunks built from
        Ollama's native NDJSON stream."""
        adapter = self.get_llm_adapter()
        params = adapter.get_llm_invocation_params(
            context,
            system_instruction=self._settings.system_instruction,
            convert_developer_to_user=not self.supports_developer_role,
        )
        messages = params.get("messages", [])
        if params.get("tools") and not getattr(self, "_warned_tools", False):
            # Log once per service instance — the wizard / main flow
            # always carries the delegate tools in context, so without
            # this dedupe we'd warn every single turn.
            logger.warning(
                "[ollama-llm] tool calls in context but the Ollama "
                "adapter does not yet translate them — proceeding "
                "content-only. See voice/llm/ollama.py."
            )
            self._warned_tools = True
        return _stream_as_openai_chunks(
            self._http,
            self._ollama_root,
            self._settings.model,
            messages,
            think=self._think,
        )


async def _stream_as_openai_chunks(
    http: httpx.AsyncClient,
    root: str,
    model: str,
    messages: list[dict],
    *,
    think: bool,
) -> AsyncIterator[Any]:
    """POST to ``/api/chat`` and translate each NDJSON line into a
    SimpleNamespace shaped like an OpenAI ChatCompletionChunk.

    The shimmed objects only carry the fields pipecat actually
    accesses (see pipecat/services/openai/base_llm.py): ``choices``,
    ``choices[0].delta.{role,content,tool_calls,audio}``,
    ``choices[0].finish_reason``, and a final ``usage`` block.
    Anything else is unset.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": think,
    }
    async with http.stream("POST", f"{root}/api/chat", json=payload) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.debug(f"[ollama-llm] non-JSON line skipped: {line[:80]!r}")
                continue

            chunk_model = data.get("model") or model
            if data.get("done"):
                # Final chunk — emit usage and finish_reason=stop.
                usage = SimpleNamespace(
                    prompt_tokens=int(data.get("prompt_eval_count") or 0),
                    completion_tokens=int(data.get("eval_count") or 0),
                    total_tokens=int(
                        (data.get("prompt_eval_count") or 0)
                        + (data.get("eval_count") or 0)
                    ),
                    # Pipecat reads `.prompt_tokens_details.cached_tokens`
                    # and `.completion_tokens_details.reasoning_tokens`
                    # — Ollama doesn't break these out. Set None and
                    # pipecat's `if .prompt_tokens_details` guard
                    # falls through to zero.
                    prompt_tokens_details=None,
                    completion_tokens_details=None,
                )
                yield SimpleNamespace(
                    model=chunk_model,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                role=None,
                                content=None,
                                tool_calls=None,
                                audio=None,
                            ),
                            finish_reason="stop",
                        )
                    ],
                    usage=usage,
                )
                continue

            msg = data.get("message") or {}
            content = msg.get("content") or ""
            role = msg.get("role")
            # Skip empty content chunks and any reasoning-only chunks
            # (Ollama emits `reasoning` even when `think: false` is
            # honored on some model builds — defensive filter).
            if not content:
                continue
            yield SimpleNamespace(
                model=chunk_model,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            role=role or "assistant",
                            content=content,
                            tool_calls=None,
                            audio=None,
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
            )
