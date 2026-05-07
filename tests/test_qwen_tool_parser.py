"""Tests for the Qwen-style tool-call streaming parser.

The parser is the load-bearing piece for MLX tool-call support — it
converts ``<tool_call>{...}</tool_call>`` text emitted by Qwen3-class
models into structured events. Lives outside ``voice/llm/mlx.py``
deliberately so its tests run on Linux CI without MLX wheels.

Coverage:
  - Plain content passes through
  - Single complete call decodes
  - Multiple back-to-back calls preserve order
  - Content-then-call mixed sequences
  - Tag boundaries that split mid-token are buffered correctly
  - Malformed JSON is dropped (not crashed on)
  - Unclosed tag at flush is dropped (not silently mis-emitted)
"""

from __future__ import annotations

import json

import pytest

from voice.llm._qwen_tool_parser import (
    ContentEvent,
    QwenToolParser,
    ToolCallEvent,
    render_chat_template_with_tools,
)


def _drain(parser: QwenToolParser, *tokens: str):
    """Feed each token, return all events emitted across the stream
    (including the flush)."""
    events = []
    for t in tokens:
        events.extend(parser.feed(t))
    events.extend(parser.flush())
    return events


# --- happy-path baselines ---------------------------------------------------


def test_plain_content_passes_through():
    events = _drain(QwenToolParser(), "hello ", "world")
    assert [type(e) for e in events] == [ContentEvent, ContentEvent]
    assert "".join(e.text for e in events) == "hello world"


def test_single_tool_call_decoded():
    body = '{"name": "delegate_to", "arguments": {"agent": "ava"}}'
    events = _drain(
        QwenToolParser(),
        f"<tool_call>{body}</tool_call>",
    )
    assert len(events) == 1
    assert isinstance(events[0], ToolCallEvent)
    assert events[0].name == "delegate_to"
    assert events[0].arguments == {"agent": "ava"}


def test_content_then_tool_call_emits_both_in_order():
    body = '{"name": "fn", "arguments": {}}'
    events = _drain(
        QwenToolParser(),
        f"Let me check. <tool_call>{body}</tool_call>",
    )
    types = [type(e) for e in events]
    assert types == [ContentEvent, ToolCallEvent]
    assert events[0].text == "Let me check. "
    assert events[1].name == "fn"


def test_multiple_back_to_back_calls():
    body_a = '{"name": "fn_a", "arguments": {"x": 1}}'
    body_b = '{"name": "fn_b", "arguments": {"y": 2}}'
    events = _drain(
        QwenToolParser(),
        f"<tool_call>{body_a}</tool_call>"
        f"<tool_call>{body_b}</tool_call>",
    )
    assert len(events) == 2
    assert all(isinstance(e, ToolCallEvent) for e in events)
    assert events[0].name == "fn_a"
    assert events[0].arguments == {"x": 1}
    assert events[1].name == "fn_b"
    assert events[1].arguments == {"y": 2}


def test_tool_call_followed_by_more_content():
    body = '{"name": "fn", "arguments": {}}'
    events = _drain(
        QwenToolParser(),
        f"<tool_call>{body}</tool_call> all set.",
    )
    assert [type(e) for e in events] == [ToolCallEvent, ContentEvent]
    assert events[1].text == " all set."


# --- streaming / boundary cases --------------------------------------------


def test_open_tag_split_across_tokens():
    """The open tag arrives in pieces — parser must buffer the prefix
    rather than leaking ``<tool_call`` into content output."""
    body = '{"name": "fn", "arguments": {}}'
    parser = QwenToolParser()
    # Stream the tag in 1-3 char chunks
    pieces = ["<", "to", "ol_", "call", ">", body, "</", "tool", "_call>"]
    events = []
    for p in pieces:
        events.extend(parser.feed(p))
    events.extend(parser.flush())
    # Exactly one event, no leaked content
    assert len(events) == 1
    assert isinstance(events[0], ToolCallEvent)
    assert events[0].name == "fn"


def test_partial_open_prefix_doesnt_emit_as_content():
    """Token ends with ``"<too"`` — that's a prefix of the open tag, so
    the parser must withhold it. Otherwise the consumer prints
    ``"hello <too"`` and the closing fragment never assembles a real
    call."""
    parser = QwenToolParser()
    events1 = parser.feed("hello <too")
    # Only "hello " is safe to emit; "<too" stays buffered.
    assert len(events1) == 1
    assert events1[0].text == "hello "

    body = '{"name": "fn", "arguments": {}}'
    events2 = parser.feed(f"l_call>{body}</tool_call>")
    events2.extend(parser.flush())
    # Now we should see the parsed call, no leftover ``<tool`` content.
    call_events = [e for e in events2 if isinstance(e, ToolCallEvent)]
    content_after = [e for e in events2 if isinstance(e, ContentEvent)]
    assert len(call_events) == 1
    assert content_after == []


def test_content_with_lone_lt_doesnt_buffer_forever():
    """A lone ``<`` that's NOT followed by a tool-call open should
    eventually flush as content, not get stuck in the buffer."""
    parser = QwenToolParser()
    # Feed a chunk that starts with `<` but resolves to non-tag text
    events1 = parser.feed("price < 100 ")
    events2 = parser.feed("dollars")
    events = events1 + events2 + parser.flush()
    assert all(isinstance(e, ContentEvent) for e in events)
    assert "".join(e.text for e in events) == "price < 100 dollars"


# --- error / robustness ----------------------------------------------------


def test_malformed_json_dropped_not_raised():
    """Models occasionally emit broken JSON (trailing commas, unquoted
    keys). The parser must drop the call, not crash the stream."""
    events = _drain(
        QwenToolParser(),
        "<tool_call>{not valid json,}</tool_call> followup text",
    )
    # No tool call; followup content still flows through.
    types = [type(e) for e in events]
    assert ToolCallEvent not in types
    contents = [e.text for e in events if isinstance(e, ContentEvent)]
    assert " followup text" in "".join(contents)


def test_missing_name_dropped():
    """A well-formed JSON object that lacks ``name`` is unusable."""
    events = _drain(
        QwenToolParser(),
        '<tool_call>{"arguments": {"x": 1}}</tool_call>',
    )
    assert all(not isinstance(e, ToolCallEvent) for e in events)


def test_empty_body_dropped():
    events = _drain(QwenToolParser(), "<tool_call></tool_call>")
    assert all(not isinstance(e, ToolCallEvent) for e in events)


def test_non_object_body_dropped():
    """A bare list or scalar at the top level isn't a valid call."""
    events = _drain(QwenToolParser(), "<tool_call>[1, 2, 3]</tool_call>")
    assert all(not isinstance(e, ToolCallEvent) for e in events)


def test_unclosed_tag_at_flush_is_dropped():
    """Stream ends mid-call (model truncated). Drop the buffer rather
    than ship a half-parsed object."""
    parser = QwenToolParser()
    parser.feed('<tool_call>{"name": "fn", "argum')
    events = parser.flush()
    assert events == []  # nothing emitted; no crash


def test_arguments_can_be_omitted():
    """Some calls take no args. ``arguments`` field absent → None
    payload (caller is responsible for stringifying to ``{}``)."""
    events = _drain(QwenToolParser(), '<tool_call>{"name": "fn"}</tool_call>')
    calls = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(calls) == 1
    assert calls[0].arguments is None


def test_arguments_preserves_complex_types():
    """Nested objects, arrays, and primitives all need to round-trip
    cleanly so the downstream JSON dump matches what the model meant."""
    payload = {
        "name": "complex_fn",
        "arguments": {
            "items": [1, 2, {"nested": True}],
            "flag": False,
            "ratio": 0.25,
        },
    }
    body = json.dumps(payload)
    events = _drain(QwenToolParser(), f"<tool_call>{body}</tool_call>")
    calls = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(calls) == 1
    assert calls[0].arguments == payload["arguments"]


# --- chat-template rendering with tools ------------------------------------


class _StubTokenizer:
    """Stand-in for a HuggingFace tokenizer's chat template behaviour.

    `accepts` lists the kwarg names the template will accept; calls
    that pass any other kwarg raise TypeError, mirroring how strict
    real templates behave when they don't recognize a kwarg.

    `record` captures the kwargs of the successful call so tests can
    assert on the negotiation path the helper took.
    """

    def __init__(self, *, accepts: set[str], output: str = "RENDERED") -> None:
        self.accepts = accepts
        self.output = output
        self.record: dict | None = None
        self.attempts: list[set] = []

    def apply_chat_template(
        self, messages, *, tokenize, add_generation_prompt, **kwargs,
    ):
        self.attempts.append(set(kwargs.keys()))
        rejected = set(kwargs.keys()) - self.accepts
        if rejected:
            raise TypeError(f"unexpected kwargs: {rejected}")
        self.record = dict(kwargs)
        return self.output


def test_render_uses_full_kwargs_when_template_accepts_them():
    tokenizer = _StubTokenizer(accepts={"tools", "enable_thinking"})
    out = render_chat_template_with_tools(
        tokenizer, [{"role": "user", "content": "hi"}], tools=[{"type": "function"}],
    )
    assert out == "RENDERED"
    assert tokenizer.record == {"tools": [{"type": "function"}], "enable_thinking": False}


def test_render_falls_back_to_tools_only_when_thinking_rejected():
    """Template accepts `tools` but not `enable_thinking` (some Qwen
    fine-tunes predate the thinking toggle). We must still render with
    tools so the model sees the schema."""
    tokenizer = _StubTokenizer(accepts={"tools"})
    render_chat_template_with_tools(
        tokenizer, [], tools=[{"type": "function"}],
    )
    assert tokenizer.record == {"tools": [{"type": "function"}]}


def test_render_falls_back_to_thinking_only_when_tools_rejected(caplog):
    """Old template — no `tools` kwarg. Falls back to enable_thinking
    only and logs a warning so operators understand why no calls fire."""
    import logging as _logging
    tokenizer = _StubTokenizer(accepts={"enable_thinking"})
    with caplog.at_level(_logging.WARNING):
        render_chat_template_with_tools(
            tokenizer, [], tools=[{"type": "function"}],
        )
    assert tokenizer.record == {"enable_thinking": False}
    assert any("rejected `tools=`" in rec.message for rec in caplog.records)


def test_render_no_tools_doesnt_warn(caplog):
    """When the caller didn't pass tools at all, the
    ``rejected `tools=` `` warning is wrong — make sure we don't emit
    it spuriously on plain content turns."""
    import logging as _logging
    tokenizer = _StubTokenizer(accepts={"enable_thinking"})
    with caplog.at_level(_logging.WARNING):
        render_chat_template_with_tools(tokenizer, [], tools=None)
    assert not any("rejected `tools=`" in rec.message for rec in caplog.records)


def test_render_falls_back_to_bare_template_when_all_kwargs_rejected():
    tokenizer = _StubTokenizer(accepts=set())
    render_chat_template_with_tools(tokenizer, [], tools=None)
    assert tokenizer.record == {}


def test_render_role_tag_fallback_when_no_template():
    """If the tokenizer raises a non-TypeError, we drop to the role-tag
    string so the caller still gets *some* prompt rather than an
    exception that crashes the whole turn."""
    class _NoTemplate:
        def apply_chat_template(self, *_, **__):
            raise ValueError("this tokenizer has no chat template")

    out = render_chat_template_with_tools(
        _NoTemplate(),
        [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
        tools=None,
    )
    assert "user: hello" in out
    assert "assistant: hi" in out


def test_render_skips_full_kwargs_when_no_tools():
    """No tools requested → don't waste a probe on the (tools, thinking)
    pair. Saves one TypeError round-trip per turn on tool-less personas."""
    tokenizer = _StubTokenizer(accepts={"enable_thinking"})
    render_chat_template_with_tools(tokenizer, [], tools=None)
    # First attempt should be enable_thinking, not the tools-bearing pair.
    assert tokenizer.attempts[0] == {"enable_thinking"}


# Suppress an unused-import warning if the test runner trims this
# file in isolation; pytest is the one that actually exercises the
# module, but the import keeps the symbol referenced for ruff.
_ = pytest
