"""MLX-LM adapter — Apple Silicon native LLM inference.

Why this exists
---------------
Ollama works fine on Apple Silicon via its llama.cpp Metal backend,
but a) it's a separate process the user has to install, b) MLX-LM
(Apple's native ML framework) is consistently faster on M-series for
the same quantization, and c) bundling MLX-LM directly into the
Python sidecar means the Mac desktop install has zero LLM-side
moving parts — model files come down from HuggingFace on first use,
same as Whisper and Kokoro do today.

The adapter mirrors ``voice/llm/ollama.py``: subclass pipecat's
``BaseOpenAILLMService``, override ``get_chat_completions`` to return
an async-iterable of OpenAI-shaped chunks, and let the rest of the
pipeline (sentence aggregator, tool calls, frame dispatch) work
unchanged.

Apple-Silicon-only
------------------
This module imports ``mlx_lm`` and ``mlx`` at module load time, so
it can ONLY be imported on macOS arm64. The factory in
``voice/llm/__init__.py`` imports it lazily and falls back to the
Ollama / OpenAI adapters elsewhere.

What's NOT supported (yet)
--------------------------
- Tool / function calling. Same gap as the Ollama adapter — the
  factory's `tools` payload comes through but we don't translate it.
  Logged once per service instance.
- True streaming token timing for pipecat's TTFB metric. The MLX
  generator yields tokens as they're produced, so we still get
  pipecat's sentence-aggregator coalescing benefit; what we don't
  get is the openai-SDK-style fine-grained TTFB hooks. Cosmetic.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

# Apple-Silicon-only deps. Don't import unconditionally — the
# factory below routes around this module on non-Mac builds.
import mlx.core as mx  # noqa: F401  — mx itself isn't used here but mlx_lm needs it imported  # type: ignore[import-not-found]
from mlx_lm import load as mlx_load, stream_generate  # type: ignore[import-not-found]

from pipecat.services.openai.base_llm import BaseOpenAILLMService

logger = logging.getLogger(__name__)


# Process-wide model cache. Keyed by HF model id so repeated session
# creation doesn't re-load the weights — for a 2B-class Q4 model
# that's ~2GB of memory + a couple seconds of init we don't want to
# repeat per voice session.
_MODELS: dict[str, tuple[Any, Any]] = {}
_MODELS_LOCK = asyncio.Lock()


async def _get_model(model_id: str):
    """Load (or return cached) (model, tokenizer) tuple for ``model_id``.

    The first call downloads + initializes; subsequent calls are
    instant. Wrapped in an asyncio lock so two concurrent sessions
    don't both kick off the same load.
    """
    cached = _MODELS.get(model_id)
    if cached is not None:
        return cached
    async with _MODELS_LOCK:
        cached = _MODELS.get(model_id)  # double-checked under lock
        if cached is not None:
            return cached
        logger.info(f"[mlx-llm] loading {model_id} (first call — may download)")
        # mlx_load is sync + does the HF download + builds the model.
        # Run in default executor so we don't block the event loop;
        # the call can take 5-30s for first-run.
        loop = asyncio.get_running_loop()
        model, tokenizer = await loop.run_in_executor(None, mlx_load, model_id)
        _MODELS[model_id] = (model, tokenizer)
        logger.info(f"[mlx-llm] {model_id} ready")
        return _MODELS[model_id]


class MLXLLMService(BaseOpenAILLMService):
    """MLX-LM adapter — runs the model in-process via Apple's MLX
    framework. Drop-in replacement for ``OpenAILLMService`` on
    Apple Silicon Mac builds."""

    def __init__(
        self,
        *,
        api_key: str = "mlx",  # unused; satisfies BaseOpenAILLMService's __init__
        base_url: str = "http://invalid.local",  # unused; same reason
        model: str,
        max_tokens: int = 512,
        **kwargs: Any,
    ):
        super().__init__(
            api_key=api_key or "mlx",
            base_url=base_url,
            model=model,
            **kwargs,
        )
        self._max_tokens = max_tokens

    async def get_chat_completions(self, context):  # type: ignore[override]
        adapter = self.get_llm_adapter()
        params = adapter.get_llm_invocation_params(
            context,
            system_instruction=self._settings.system_instruction,
            convert_developer_to_user=not self.supports_developer_role,
        )
        messages = params.get("messages", [])
        if params.get("tools") and not getattr(self, "_warned_tools", False):
            logger.warning(
                "[mlx-llm] tool calls in context but the MLX adapter "
                "does not yet translate them — proceeding content-only."
            )
            self._warned_tools = True
        max_tokens = (
            int(self._settings.max_tokens or self._max_tokens)
            if hasattr(self._settings, "max_tokens")
            else self._max_tokens
        )
        return _stream_as_openai_chunks(self._settings.model, messages, max_tokens)


async def _stream_as_openai_chunks(
    model_id: str,
    messages: list[dict],
    max_tokens: int,
) -> AsyncIterator[Any]:
    """Run mlx-lm's stream_generate and yield SimpleNamespace chunks
    matching the subset of ChatCompletionChunk pipecat reads.

    Token generation runs synchronously inside MLX; we trampoline it
    through `loop.run_in_executor` chunk-by-chunk so the FastAPI
    event loop stays responsive (web requests, WebRTC ICE, etc. don't
    starve while the model is decoding)."""
    model, tokenizer = await _get_model(model_id)
    # Apply the model's chat template so the prompt matches what the
    # model was instruction-tuned for. Pass `enable_thinking=False` —
    # Qwen3 / DeepSeek-R1 / similar reasoning models accept it as a
    # template kwarg and drop the `<think>...</think>` preamble that
    # would otherwise jam pipecat's sentence aggregator (TTS waits
    # for sentence-break tokens that never arrive in the reasoning
    # phase). Non-thinking models silently ignore unknown kwargs;
    # try with the flag first, fall back without if the tokenizer
    # raises (some templates are strict about unrecognized kwargs).
    try:
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
            enable_thinking=False,
        )
    except TypeError:
        # Older / strict templates don't accept enable_thinking — retry
        # without. Models that don't have a thinking phase don't need
        # the flag anyway.
        try:
            prompt = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True,
            )
        except Exception:
            prompt = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in messages)
    except Exception:
        # Last-ditch: stuff messages together with role tags.
        prompt = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in messages)

    loop = asyncio.get_running_loop()

    # `stream_generate` is a synchronous Python generator; iterate it
    # in an executor and pump chunks across.
    #
    # Cross-thread bridge: the producer runs on a thread-pool executor,
    # so it can't touch the asyncio.Queue directly — `put_nowait` and
    # friends are only safe from the event-loop thread. Schedule each
    # put via `run_coroutine_threadsafe` and block the producer on the
    # returned Future so backpressure flows the right way (queue full →
    # producer waits → MLX generator pauses naturally between tokens).
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    final_meta: dict[str, Any] = {}

    def _put(item: tuple[str, Any]) -> None:
        """Cross-thread put with backpressure. Blocks the producer
        thread until the asyncio side accepts the item."""
        try:
            asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()
        except Exception as e:  # noqa: BLE001 — loop-shutdown race
            # Loop went away mid-generation (consumer cancelled). Log
            # once and let _producer's `finally` end gracefully.
            logger.debug(f"[mlx-llm] queue put dropped (loop closed?): {e}")

    def _producer():
        n_tokens = 0
        n_text_chunks = 0
        try:
            with mx.stream(mx.gpu):
                for resp in stream_generate(
                    model, tokenizer, prompt, max_tokens=max_tokens
                ):
                    n_tokens += 1
                    text = getattr(resp, "text", "") or ""
                    if text:
                        n_text_chunks += 1
                        _put(("token", text))
                    final_meta["prompt_tokens"] = getattr(resp, "prompt_tokens", None)
                    final_meta["generation_tokens"] = getattr(resp, "generation_tokens", None)
            logger.info(
                f"[mlx-llm] generation done: {n_tokens} steps, "
                f"{n_text_chunks} text chunks emitted"
            )
        except Exception as e:
            logger.exception(f"[mlx-llm] generation crashed after {n_tokens} steps")
            _put(("error", str(e)))
        finally:
            _put(("done", None))

    fut = loop.run_in_executor(None, _producer)

    try:
        while True:
            kind, payload = await queue.get()
            if kind == "done":
                break
            if kind == "error":
                logger.error(f"[mlx-llm] generation failed: {payload}")
                break
            yield SimpleNamespace(
                model=model_id,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            role="assistant",
                            content=payload,
                            tool_calls=None,
                            audio=None,
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
            )
        # Final chunk with usage + finish_reason=stop.
        prompt_tokens = int(final_meta.get("prompt_tokens") or 0)
        completion_tokens = int(final_meta.get("generation_tokens") or 0)
        yield SimpleNamespace(
            model=model_id,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        role=None, content=None, tool_calls=None, audio=None,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                prompt_tokens_details=None,
                completion_tokens_details=None,
            ),
        )
    finally:
        # Make sure the executor task drains even if the consumer
        # broke out early. We log at DEBUG rather than swallowing
        # silently — a producer crash here is rare but surfaces a
        # real bug (model corruption, GPU OOM) that's worth a note
        # in verbose logs even though it can't propagate up to the
        # caller anymore (the stream is closed).
        try:
            await fut
        except Exception as e:  # noqa: BLE001 — best-effort drain
            logger.debug(f"[mlx-llm] producer drain error (ignored): {e}")
