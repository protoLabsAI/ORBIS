"""Tests for the Ollama LLM adapter — tool-call translation.

Stub Ollama's NDJSON stream via respx + iterate the adapter's async
generator. Asserts on the OpenAI-shape SimpleNamespace chunks pipecat
reads (see voice/llm/ollama.py module docstring for the contract).

The plain-content path is exercised implicitly by every tool-call test
case (a typical model emits some text before / instead of calling a
tool). The closing-chunk semantics — finish_reason flips to
"tool_calls" iff a tool call flowed — is the load-bearing invariant.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from voice.llm.ollama import _stream_as_openai_chunks, _stringify_args


# --- _stringify_args truth table --------------------------------------------


def test_stringify_args_dict_to_json():
    assert _stringify_args({"a": 1, "b": "x"}) == json.dumps({"a": 1, "b": "x"})


def test_stringify_args_passes_through_str():
    """Some ollama-server forks have been seen handing back a
    pre-stringified blob; we should respect it instead of double-encoding."""
    assert _stringify_args('{"already": "a string"}') == '{"already": "a string"}'


def test_stringify_args_none_to_empty_object():
    assert _stringify_args(None) == "{}"


def test_stringify_args_unserializable_falls_back_to_empty():
    # set() is not JSON-serializable — the fallback shouldn't crash the
    # pipeline, just emit "{}" so the call surfaces but the handler can
    # decide what to do with the empty payload.
    assert _stringify_args({1, 2, 3}) == "{}"


# --- streaming translation --------------------------------------------------


def _ndjson(*chunks: dict) -> bytes:
    return ("".join(json.dumps(c) + "\n" for c in chunks)).encode()


async def _drain(http, root, model, messages, *, think=False, tools=None):
    out = []
    async for chunk in _stream_as_openai_chunks(
        http, root, model, messages, think=think, tools=tools
    ):
        out.append(chunk)
    return out


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def http():
    """A real httpx.AsyncClient — respx patches the transport at the
    httpx level so our adapter still goes through stream() normally."""
    return httpx.AsyncClient(timeout=5.0)


def test_content_only_emits_stop_finish(http, respx_mock):
    """Sanity baseline: text-only response keeps finish_reason='stop'."""
    body = _ndjson(
        {"message": {"role": "assistant", "content": "hi "}, "done": False},
        {"message": {"role": "assistant", "content": "there"}, "done": False},
        {
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 2,
        },
    )
    respx_mock.post("http://ollama/api/chat").respond(
        status_code=200, content=body,
    )

    chunks = _run(_drain(http, "http://ollama", "m", [{"role": "user", "content": "hi"}]))
    contents = [c.choices[0].delta.content for c in chunks if c.choices[0].delta.content]
    assert contents == ["hi ", "there"]
    last = chunks[-1]
    assert last.choices[0].finish_reason == "stop"
    assert last.usage.prompt_tokens == 5
    assert last.usage.completion_tokens == 2


def test_tool_call_emits_openai_shape(http, respx_mock):
    """Single tool call → one OpenAI-shaped delta chunk + tool_calls finish."""
    body = _ndjson(
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "delegate_to",
                            "arguments": {"agent": "ava", "request": "weather"},
                        }
                    }
                ],
            },
            "done": False,
        },
        {"done": True, "prompt_eval_count": 9, "eval_count": 3},
    )
    respx_mock.post("http://ollama/api/chat").respond(status_code=200, content=body)

    chunks = _run(_drain(
        http, "http://ollama", "m",
        [{"role": "user", "content": "ask ava about weather"}],
        tools=[{"type": "function", "function": {"name": "delegate_to"}}],
    ))

    # First non-empty chunk is the tool_call delta. Pipecat reads:
    # delta.tool_calls[0].{index,id,function.name,function.arguments}
    tool_chunks = [c for c in chunks if c.choices[0].delta.tool_calls]
    assert len(tool_chunks) == 1
    tc = tool_chunks[0].choices[0].delta.tool_calls[0]
    assert tc.index == 0
    assert tc.id.startswith("call_")
    assert tc.type == "function"
    assert tc.function.name == "delegate_to"
    # arguments must be JSON STRING, not dict — pipecat += accumulates.
    assert tc.function.arguments == json.dumps({"agent": "ava", "request": "weather"})

    # Closing chunk — finish_reason flips to tool_calls so pipecat closes
    # the accumulator and dispatches the call.
    last = chunks[-1]
    assert last.choices[0].finish_reason == "tool_calls"


def test_multiple_tool_calls_get_distinct_indices(http, respx_mock):
    """Parallel tool calls → indices 0, 1, ... so pipecat's accumulator
    keeps them separate."""
    body = _ndjson(
        {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "fn_a", "arguments": {"x": 1}}},
                    {"function": {"name": "fn_b", "arguments": {"y": 2}}},
                ],
            },
            "done": False,
        },
        {"done": True},
    )
    respx_mock.post("http://ollama/api/chat").respond(status_code=200, content=body)

    chunks = _run(_drain(http, "http://ollama", "m", []))
    tool_chunks = [c for c in chunks if c.choices[0].delta.tool_calls]
    assert len(tool_chunks) == 2
    assert tool_chunks[0].choices[0].delta.tool_calls[0].index == 0
    assert tool_chunks[0].choices[0].delta.tool_calls[0].function.name == "fn_a"
    assert tool_chunks[1].choices[0].delta.tool_calls[0].index == 1
    assert tool_chunks[1].choices[0].delta.tool_calls[0].function.name == "fn_b"
    assert chunks[-1].choices[0].finish_reason == "tool_calls"


def test_content_then_tool_call_keeps_both(http, respx_mock):
    """Some models narrate before calling — we must surface BOTH the
    content delta(s) and the tool_call so pipecat speaks the narration
    AND dispatches the call."""
    body = _ndjson(
        {"message": {"role": "assistant", "content": "Let me check. "}, "done": False},
        {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "delegate_to", "arguments": {"agent": "ava"}}}
                ],
            },
            "done": False,
        },
        {"done": True},
    )
    respx_mock.post("http://ollama/api/chat").respond(status_code=200, content=body)

    chunks = _run(_drain(http, "http://ollama", "m", []))
    contents = [c.choices[0].delta.content for c in chunks if c.choices[0].delta.content]
    assert contents == ["Let me check. "]
    tool_chunks = [c for c in chunks if c.choices[0].delta.tool_calls]
    assert len(tool_chunks) == 1
    assert chunks[-1].choices[0].finish_reason == "tool_calls"


def test_tools_param_forwarded_to_ollama(http, respx_mock):
    """When tools are provided we must pass them through; an Ollama
    that never sees `tools` will never call one."""
    schema = [{
        "type": "function",
        "function": {
            "name": "delegate_to",
            "description": "hand off to a sub-agent",
            "parameters": {"type": "object", "properties": {"agent": {"type": "string"}}},
        },
    }]
    captured = {}

    def _capture(req: httpx.Request):
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, content=_ndjson({"done": True}))

    respx_mock.post("http://ollama/api/chat").mock(side_effect=_capture)

    _run(_drain(http, "http://ollama", "m",
                [{"role": "user", "content": "x"}], tools=schema))

    assert captured["body"].get("tools") == schema


def test_tools_omitted_when_empty(http, respx_mock):
    """No tools in context → don't send the tools field at all. Sending
    an empty list flips Ollama into a tool-aware codepath and changes
    sampling on some model builds, which we shouldn't pay on plain
    content turns."""
    captured = {}

    def _capture(req: httpx.Request):
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, content=_ndjson({"done": True}))

    respx_mock.post("http://ollama/api/chat").mock(side_effect=_capture)

    _run(_drain(http, "http://ollama", "m", [], tools=None))
    assert "tools" not in captured["body"]

    captured.clear()
    respx_mock.post("http://ollama/api/chat").mock(side_effect=_capture)
    _run(_drain(http, "http://ollama", "m", [], tools=[]))
    assert "tools" not in captured["body"]


def test_string_arguments_passed_through_unchanged(http, respx_mock):
    """Some ollama-server builds hand back arguments as a stringified
    blob already; we must NOT double-encode."""
    body = _ndjson(
        {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "fn", "arguments": '{"already": "string"}'}}
                ],
            },
            "done": False,
        },
        {"done": True},
    )
    respx_mock.post("http://ollama/api/chat").respond(status_code=200, content=body)

    chunks = _run(_drain(http, "http://ollama", "m", []))
    tc = next(c for c in chunks if c.choices[0].delta.tool_calls).choices[0].delta.tool_calls[0]
    assert tc.function.arguments == '{"already": "string"}'
