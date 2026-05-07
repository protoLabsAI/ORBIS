"""Streaming parser for Qwen-style ``<tool_call>{...}</tool_call>`` tags.

Lives outside ``voice/llm/mlx.py`` deliberately — that module imports
``mlx_lm`` at top, so it's darwin-arm64-only. The parser is pure
Python with no platform-specific deps, which lets its tests run on
Linux CI without the MLX wheels.

Why this exists
---------------
MLX-LM yields tokens as plain text. Modern instruction-tuned models
(Qwen2.5+, Qwen3, DeepSeek, several Llama-3 fine-tunes) emit tool calls
as text with this convention, applied by their chat template:

    Optional preamble.
    <tool_call>
    {"name": "delegate_to", "arguments": {"agent": "ava"}}
    </tool_call>

Parallel calls just stack:

    <tool_call>{"name": "fn_a", ...}</tool_call>
    <tool_call>{"name": "fn_b", ...}</tool_call>

Pipecat consumes function calls as structured ``delta.tool_calls`` on
streamed chunks (see voice/llm/ollama.py for the contract). We can't
just emit the whole text and hope — pipecat's sentence aggregator would
read ``<tool_call>...`` literally and ship it to TTS, which is both
embarrassing and breaks the pipeline (no real call ever fires).

The parser converts the streamed text into a sequence of typed events
the caller turns into OpenAI-shaped chunks. Tag boundaries can split
mid-token, so the buffer holds enough trailing characters to detect a
partial tag prefix before flushing content.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# The conventional Qwen tag pair. Hard-coded because the chat templates
# that use this format all settle on the same literal — if a future
# model uses different markers we'd want a different parser anyway
# (the framing rules differ enough to merit separate handling).
_OPEN_TAG = "<tool_call>"
_CLOSE_TAG = "</tool_call>"


@dataclass
class ContentEvent:
    """Plain narration text from the model — the kind that should reach
    TTS unchanged."""
    text: str


@dataclass
class ToolCallEvent:
    """A complete, parsed tool call. ``arguments`` is whatever the model
    emitted as JSON — typically a dict, but loose models occasionally
    emit a list or a primitive. Caller is responsible for stringifying
    when handing off to pipecat (which expects a JSON string)."""
    name: str
    arguments: object


# Public type alias for callers — they branch on isinstance().
ParserEvent = ContentEvent | ToolCallEvent


class QwenToolParser:
    """Streaming state machine for ``<tool_call>...</tool_call>`` blocks.

    Usage::

        parser = QwenToolParser()
        for token in incoming_stream:
            for event in parser.feed(token):
                ...
        for event in parser.flush():
            ...

    Boundary handling: when a token ends with a partial prefix of the
    open tag (e.g. ``"...<tool"``), the parser keeps that prefix in the
    buffer instead of emitting it as content — emitting prematurely
    would leak ``<tool`` into TTS output and the next token's ``_call>``
    would be parsed as content too, missing the call entirely.
    """

    def __init__(self) -> None:
        self._buf: str = ""
        # ``in_call`` is True while we're between an open tag and its
        # matching close tag — accumulating JSON body in self._buf.
        self._in_call: bool = False

    def feed(self, token: str) -> list[ParserEvent]:
        """Append the token to the running buffer and yield any events
        whose boundaries are now fully visible."""
        if not token:
            return []
        self._buf += token
        events: list[ParserEvent] = []
        # Loop because one token can carry multiple boundaries —
        # e.g. a very small model that races through the format and
        # emits ``<tool_call>{...}</tool_call><tool_call>{...}`` in a
        # single tokenizer chunk. Each loop iteration consumes at least
        # one tag boundary; we exit when the buffer has no more
        # actionable boundaries.
        while True:
            if self._in_call:
                close_at = self._buf.find(_CLOSE_TAG)
                if close_at == -1:
                    # Body still arriving; wait for more.
                    return events
                body = self._buf[:close_at].strip()
                self._buf = self._buf[close_at + len(_CLOSE_TAG):]
                self._in_call = False
                event = _parse_call_body(body)
                if event is not None:
                    events.append(event)
                continue

            open_at = self._buf.find(_OPEN_TAG)
            if open_at != -1:
                # Emit any narration ahead of the call as content,
                # then consume the open tag and switch state.
                if open_at > 0:
                    events.append(ContentEvent(text=self._buf[:open_at]))
                self._buf = self._buf[open_at + len(_OPEN_TAG):]
                self._in_call = True
                continue

            # No open tag found; emit only the prefix that's safe to
            # release. A "safe" prefix is everything except a possible
            # partial open-tag suffix at the end of the buffer.
            keep = _max_partial_open_prefix(self._buf)
            if keep == len(self._buf):
                # Whole buffer might still resolve into a tag — wait.
                return events
            if keep == 0:
                events.append(ContentEvent(text=self._buf))
                self._buf = ""
            else:
                events.append(ContentEvent(text=self._buf[:-keep]))
                self._buf = self._buf[-keep:]
            return events

    def flush(self) -> list[ParserEvent]:
        """Drain any remaining buffered text at end-of-stream.

        Unclosed tool-call buffers are dropped with a warning — the
        model truncated mid-call and we have nothing useful to emit.
        Better to lose the call than to ship a half-parsed object that
        downstream code might misinterpret.
        """
        if self._in_call:
            if self._buf:
                logger.warning(
                    "[qwen-tool-parser] unclosed <tool_call> at end of stream "
                    f"(discarded {len(self._buf)} chars)"
                )
            self._buf = ""
            self._in_call = False
            return []
        if self._buf:
            tail = self._buf
            self._buf = ""
            return [ContentEvent(text=tail)]
        return []


def _parse_call_body(body: str) -> ToolCallEvent | None:
    """Decode the JSON between an open/close tag pair into a
    ToolCallEvent. Models sometimes emit slightly malformed JSON
    (trailing commas, unquoted keys); we don't try to fix those — the
    proper response is an empty / failed call so the caller can decide
    whether to retry or surface the failure to the user."""
    if not body:
        logger.warning("[qwen-tool-parser] empty <tool_call> body")
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        logger.warning(
            f"[qwen-tool-parser] JSON decode failed ({e}); body={body[:200]!r}"
        )
        return None
    if not isinstance(parsed, dict):
        logger.warning(
            f"[qwen-tool-parser] tool_call body not an object: {parsed!r}"
        )
        return None
    name = parsed.get("name")
    if not isinstance(name, str) or not name:
        logger.warning(f"[qwen-tool-parser] missing tool name: {parsed!r}")
        return None
    return ToolCallEvent(name=name, arguments=parsed.get("arguments"))


def render_chat_template_with_tools(
    tokenizer: Any, messages: list[dict], tools: list[dict] | None,
) -> str:
    """Apply a HuggingFace tokenizer's chat template with graceful
    fallback for older / strict templates.

    Lives next to the parser because the two are paired: this renders
    the tools schema into the prompt so the model knows what to call,
    the parser reads the resulting ``<tool_call>...</tool_call>``
    blocks back out of the stream.

    Order of attempts:
      1. Full kwargs: ``tools=...`` + ``enable_thinking=False``. Modern
         Qwen3+ / DeepSeek-R1 templates accept both. The first lets the
         model see the function schemas; the second drops the
         ``<think>...</think>`` preamble that jams pipecat's sentence
         aggregator.
      2. ``tools`` alone (drop ``enable_thinking``). Some templates
         predate the thinking-mode toggle.
      3. ``enable_thinking`` alone (drop ``tools``). Older templates
         that don't model tool calls — falling back this way means the
         model won't know about the schema and won't emit calls,
         matching the pre-tool era behaviour.
      4. Bare template — no extras.
      5. Last-ditch role-tag concatenation. Only reached if the
         tokenizer has no chat template at all (rare).

    Logs a warning when ``tools`` were requested but the template
    silently rejected them — so an operator investigating "why isn't
    the model calling tools" sees a clear signal in the logs.
    """
    template_kwargs_seq: list[dict[str, Any]] = []
    if tools:
        template_kwargs_seq.append({"tools": tools, "enable_thinking": False})
        template_kwargs_seq.append({"tools": tools})
    template_kwargs_seq.append({"enable_thinking": False})
    template_kwargs_seq.append({})

    for kwargs in template_kwargs_seq:
        try:
            rendered = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True, **kwargs,
            )
        except TypeError:
            # Template rejected one of the kwargs; try the next subset.
            continue
        except Exception as e:
            logger.warning(f"[chat-template] render failed: {e}")
            break
        if tools and "tools" not in kwargs:
            # Asked-for tools but the template only accepted a subset
            # that excludes them — the model won't see the schema and
            # won't emit calls. Surface this so operators investigating
            # "why isn't the model calling tools" get a clear signal.
            logger.warning(
                "[chat-template] tokenizer rejected `tools=` kwarg; tool "
                "calls won't fire on this model. Update the template or "
                "switch to a tool-aware checkpoint (Qwen3+/DeepSeek-R1+/etc)."
            )
        return rendered

    return "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages
    )


def _max_partial_open_prefix(buf: str) -> int:
    """Return the length of the longest suffix of ``buf`` that is a
    prefix of ``_OPEN_TAG``. This is how much we must withhold to avoid
    leaking an in-flight tag into the content stream.

    Example: buf=``"hello <too"`` → returns 4 (``"<too"`` is a prefix
    of ``"<tool_call>"``). Caller emits ``"hello "`` and keeps ``"<too"``
    pending the next token.
    """
    max_check = min(len(_OPEN_TAG) - 1, len(buf))
    for i in range(max_check, 0, -1):
        if buf.endswith(_OPEN_TAG[:i]):
            return i
    return 0
