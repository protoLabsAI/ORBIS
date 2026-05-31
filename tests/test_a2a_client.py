"""Unit tests for the first-class A2AClient (a2a/client.py).

Covers agent-card discovery + caching, transport selection, capability-driven
streaming, sticky contextId continuity, and Task/Message result parsing
(including input-required and both terminal-state spellings). httpx is mocked
with respx — no network.
"""

import httpx
import pytest
import respx

from a2a.client import (
    A2AClient,
    A2ADispatchError,
    A2AResult,
    A2ATransport,
    _is_terminal,
    _parse_result_object,
)

CARD_URL = "http://ava:3008/.well-known/agent-card.json"
RPC_URL = "http://ava:3008/a2a"


def _client(**kw) -> A2AClient:
    return A2AClient(
        RPC_URL,
        headers={"X-API-Key": "k"},
        card_origin="http://ava:3008",
        name="ava",
        **kw,
    )


def _send_result(text, *, task_id="t1", context_id="ctx-remote", state="completed"):
    """A JSON-RPC message/send response whose result is an A2A Task."""
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "id": task_id,
            "contextId": context_id,
            "status": {"state": state},
            "artifacts": [{"parts": [{"kind": "text", "text": text}]}],
        },
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_terminal_states_both_spellings():
    assert _is_terminal({"state": "canceled"})    # spec spelling
    assert _is_terminal({"state": "cancelled"})   # british
    assert _is_terminal({"state": "completed"})
    assert not _is_terminal({"state": "working"})
    assert not _is_terminal({"state": "input-required"})


def test_parse_result_input_required():
    r = _parse_result_object(
        {
            "id": "t9",
            "contextId": "c9",
            "status": {"state": "input-required"},
            "status_message": {},
        },
        fallback_context_id="fb",
    )
    assert r.input_required and not r.is_terminal
    assert r.task_id == "t9" and r.context_id == "c9"


def test_parse_result_falls_back_to_context():
    r = _parse_result_object(
        {"status": {"state": "completed"},
         "artifacts": [{"parts": [{"kind": "text", "text": "hi"}]}]},
        fallback_context_id="fb",
    )
    assert r.text == "hi" and r.context_id == "fb" and r.is_terminal


# ---------------------------------------------------------------------------
# Agent card discovery + caching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_agent_card_fetched_once_and_cached():
    route = respx.get(CARD_URL).respond(
        json={"name": "ava", "capabilities": {"streaming": True}}
    )
    c = _client()
    card1 = await c.agent_card()
    card2 = await c.agent_card()
    assert card1 == card2
    assert (await c.supports_streaming()) is True
    assert route.call_count == 1  # cached, not re-fetched


@pytest.mark.asyncio
@respx.mock
async def test_agent_card_failure_is_tolerated():
    respx.get(CARD_URL).mock(side_effect=httpx.ConnectError("down"))
    c = _client()
    assert await c.agent_card() is None
    assert (await c.supports_streaming()) is False  # conservative default


@pytest.mark.asyncio
@respx.mock
async def test_preferred_transport_from_card():
    respx.get(CARD_URL).respond(json={"preferredTransport": "JSONRPC"})
    assert (await _client().preferred_transport()) is A2ATransport.JSONRPC


@pytest.mark.asyncio
@respx.mock
async def test_unsupported_transport_raises():
    respx.get(CARD_URL).respond(json={"preferredTransport": "GRPC"})
    with pytest.raises(A2ADispatchError, match="GRPC"):
        await _client().send("hi")


# ---------------------------------------------------------------------------
# send() — sync path, parsing, contextId continuity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_send_sync_returns_full_result():
    respx.get(CARD_URL).respond(json={"capabilities": {"streaming": False}})
    respx.post(RPC_URL).respond(json=_send_result("the fleet is green"))
    res = await _client().send("status?")
    assert isinstance(res, A2AResult)
    assert res.text == "the fleet is green"
    assert res.task_id == "t1" and res.state == "completed" and res.is_terminal


@pytest.mark.asyncio
@respx.mock
async def test_send_threads_sticky_context_id_across_calls():
    respx.get(CARD_URL).respond(json={"capabilities": {"streaming": False}})
    seen = []

    def _capture(request):
        import json as _j
        seen.append(_j.loads(request.content)["params"]["contextId"])
        return httpx.Response(200, json=_send_result("ok"))

    respx.post(RPC_URL).mock(side_effect=_capture)
    c = _client()
    await c.send("one")
    await c.send("two")
    assert seen[0] == seen[1] == c.context_id  # same sticky context both turns


@pytest.mark.asyncio
@respx.mock
async def test_explicit_context_id_overrides_sticky():
    respx.get(CARD_URL).respond(json={"capabilities": {"streaming": False}})
    seen = []

    def _capture(request):
        import json as _j
        seen.append(_j.loads(request.content)["params"]["contextId"])
        return httpx.Response(200, json=_send_result("ok"))

    respx.post(RPC_URL).mock(side_effect=_capture)
    c = _client()
    await c.send("one", context_id="explicit-123")
    assert seen[0] == "explicit-123" and seen[0] != c.context_id


@pytest.mark.asyncio
@respx.mock
async def test_send_input_required_returns_handle_without_raising():
    respx.get(CARD_URL).respond(json={"capabilities": {"streaming": False}})
    respx.post(RPC_URL).respond(
        json={"jsonrpc": "2.0", "id": "1", "result": {
            "id": "t2", "contextId": "c2",
            "status": {"state": "input-required",
                       "message": {"parts": [{"kind": "text", "text": "which env?"}]}},
        }}
    )
    res = await _client().send("deploy")
    assert res.input_required and res.task_id == "t2"
    assert res.text == "which env?" and not res.is_terminal


# ---------------------------------------------------------------------------
# send() — streaming path (capability-driven) + fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_send_streams_when_card_advertises_streaming():
    respx.get(CARD_URL).respond(json={"capabilities": {"streaming": True}})
    sse = (
        'data: {"jsonrpc":"2.0","result":{"kind":"task-status-update",'
        '"final":true,"status":{"state":"completed","message":'
        '{"parts":[{"kind":"text","text":"streamed answer"}]}}}}\n\n'
    )
    respx.post(RPC_URL).respond(
        200, headers={"Content-Type": "text/event-stream"}, content=sse
    )
    res = await _client().send("go")
    assert res.text == "streamed answer"


@pytest.mark.asyncio
@respx.mock
async def test_stream_error_falls_back_to_sync():
    respx.get(CARD_URL).respond(json={"capabilities": {"streaming": True}})
    # First (stream) POST 500s → A2ADispatchError → sync retry succeeds.
    route = respx.post(RPC_URL)
    route.side_effect = [
        httpx.Response(500, text="boom"),
        httpx.Response(200, json=_send_result("sync fallback")),
    ]
    res = await _client().send("go")
    assert res.text == "sync fallback"
