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

Tool calling
------------
Ollama's native ``/api/chat`` accepts a ``tools`` request param with
the same OpenAI-compat schema pipecat already produces, and emits
``message.tool_calls`` in the streamed response when the model decides
to call a function. Two shape differences pipecat doesn't tolerate:

  - Ollama returns ``arguments`` as a parsed JSON object; OpenAI
    streams it as a stringified JSON delta. We ``json.dumps`` to bridge.
  - Ollama emits the whole tool_calls list atomically in one chunk;
    OpenAI streams ``id``+``name`` first then accumulates ``arguments``
    fragments. Pipecat's loop tolerates a single fat chunk per call as
    long as the shape matches, so we don't bother fragmenting.
  - Ollama omits a tool-call ``id``. We synthesize ``call_<uuid>`` per
    call so the assistant→tool→assistant turn-roundtrip matching works.

Pipecat closes the call set on ``finish_reason="tool_calls"``; we emit
that instead of ``"stop"`` whenever any tool_calls flowed.

Other deltas from OpenAI (still not bridged):

- Vision / multimodal. Ollama supports image inputs on multimodal
  models; we ignore them for now (voice is text-in, text-out).
- ``stream_options.include_usage``. Ollama provides usage in the
  final ``done`` chunk natively; we surface it on the closing chunk.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
from pipecat.frames.frames import CancelFrame, EndFrame
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

    async def stop(self, frame: EndFrame):
        """Close the per-instance AsyncClient on graceful shutdown.
        Each voice session constructs a fresh OllamaLLMService, and
        without this every disconnect leaks the keep-alive pool until
        GC eventually runs."""
        await self._aclose_http()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame):
        """Same teardown on hard cancellation — pipecat fires either
        ``stop`` (clean) or ``cancel`` (interrupted) but not both."""
        await self._aclose_http()
        await super().cancel(frame)

    async def _aclose_http(self) -> None:
        """Idempotent close. Pipecat can call stop and cancel back-to-back
        in some teardown paths; the second call should be a no-op."""
        client = getattr(self, "_http", None)
        if client is None:
            return
        try:
            await client.aclose()
        except Exception as e:  # noqa: BLE001 — defensive on shutdown path
            logger.debug(f"[ollama-llm] aclose error (ignored): {e}")
        self._http = None

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
        # OpenAI tools schema is wire-compatible with Ollama's /api/chat
        # tools field, so we forward it directly. None / empty → omit
        # so we don't trigger Ollama's tool-aware codepath needlessly.
        tools = params.get("tools") or None
        return _stream_as_openai_chunks(
            self._http,
            self._ollama_root,
            self._settings.model,
            messages,
            think=self._think,
            tools=tools,
        )


async def _stream_as_openai_chunks(
    http: httpx.AsyncClient,
    root: str,
    model: str,
    messages: list[dict],
    *,
    think: bool,
    tools: list[dict] | None = None,
) -> AsyncIterator[Any]:
    """POST to ``/api/chat`` and translate each NDJSON line into a
    SimpleNamespace shaped like an OpenAI ChatCompletionChunk.

    The shimmed objects only carry the fields pipecat actually
    accesses (see pipecat/services/openai/base_llm.py): ``choices``,
    ``choices[0].delta.{role,content,tool_calls,audio}``,
    ``choices[0].finish_reason``, and a final ``usage`` block.
    Anything else is unset.

    When ``tools`` is non-empty it's forwarded to Ollama; any
    ``message.tool_calls`` in the response are translated into the
    OpenAI delta shape pipecat reads, with synthetic ``call_<uuid>``
    ids and JSON-stringified arguments. The closing chunk's
    ``finish_reason`` flips from ``"stop"`` to ``"tool_calls"``
    whenever a tool call flowed, so pipecat's loop closes the
    accumulator the way it expects.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": think,
    }
    if tools:
        payload["tools"] = tools
    saw_tool_call = False
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
                # Final chunk — emit usage and the right finish_reason.
                # Pipecat closes the tool-call accumulator on
                # `tool_calls`; closing on `stop` instead would silently
                # discard the call. Inverse for plain content turns.
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
                            finish_reason="tool_calls" if saw_tool_call else "stop",
                        )
                    ],
                    usage=usage,
                )
                continue

            msg = data.get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content") or ""
            role = msg.get("role")

            if tool_calls:
                # Ollama ships the whole call list atomically. Emit each
                # as its own OpenAI-shaped delta with the matching
                # `index`; pipecat's loop already supports >1 call per
                # turn (e.g. parallel tool use) and closes them out
                # together when finish_reason="tool_calls" arrives.
                saw_tool_call = True
                for idx, tc in enumerate(tool_calls):
                    fn = (tc or {}).get("function") or {}
                    name = fn.get("name") or ""
                    raw_args = fn.get("arguments")
                    args_str = _stringify_args(raw_args)
                    yield SimpleNamespace(
                        model=chunk_model,
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    role=role or "assistant",
                                    content=None,
                                    tool_calls=[
                                        SimpleNamespace(
                                            index=idx,
                                            id=f"call_{uuid.uuid4().hex[:24]}",
                                            type="function",
                                            function=SimpleNamespace(
                                                name=name,
                                                arguments=args_str,
                                            ),
                                        )
                                    ],
                                    audio=None,
                                ),
                                finish_reason=None,
                            )
                        ],
                        usage=None,
                    )

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


def _stringify_args(raw: Any) -> str:
    """Coerce Ollama's tool-call arguments to the JSON string shape
    pipecat / OpenAI expect. Ollama returns a parsed dict in the happy
    path, but older builds + 3rd-party model-server forks have been
    seen returning a pre-stringified blob — accept both. Anything
    else (None, list, weird primitive) round-trips through json.dumps
    so the downstream consumer always sees a valid JSON document."""
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw if raw is not None else {})
    except (TypeError, ValueError):
        # Last-ditch: stringify the repr so the call doesn't crash the
        # pipeline. The handler will likely reject it, but a logged
        # failure beats a silent dropped call.
        logger.warning(f"[ollama-llm] non-JSON-serializable tool args: {raw!r}")
        return "{}"
