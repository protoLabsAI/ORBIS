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


# ---------------------------------------------------------------------------
# PR-2 — task lifecycle (continuation, get/cancel, poll)
# ---------------------------------------------------------------------------

def _rpc(request):
    import json as _j
    return _j.loads(request.content)


@pytest.mark.asyncio
@respx.mock
async def test_continue_task_resends_same_task_and_context():
    respx.get(CARD_URL).respond(json={"capabilities": {"streaming": False}})
    captured = {}

    def _capture(request):
        body = _rpc(request)
        captured.update(body["params"])
        return httpx.Response(200, json=_send_result("done", task_id="t2", context_id="c2"))

    respx.post(RPC_URL).mock(side_effect=_capture)
    prior = A2AResult(text="which env?", state="input-required", task_id="t2",
                      context_id="c2", input_required=True)
    res = await _client().continue_task(prior, "production")
    assert captured["taskId"] == "t2" and captured["contextId"] == "c2"
    assert captured["message"]["taskId"] == "t2"  # also on the message
    assert res.text == "done" and res.is_terminal


@pytest.mark.asyncio
@respx.mock
async def test_continuation_never_streams_even_if_card_supports_it():
    # Card advertises streaming, but a taskId continuation must go sync.
    respx.get(CARD_URL).respond(json={"capabilities": {"streaming": True}})
    seen_methods = []

    def _capture(request):
        seen_methods.append(_rpc(request)["method"])
        return httpx.Response(200, json=_send_result("ok"))

    respx.post(RPC_URL).mock(side_effect=_capture)
    await _client().send("answer", task_id="t9", context_id="c9")
    assert seen_methods == ["message/send"]  # plain sync, not a stream


@pytest.mark.asyncio
@respx.mock
async def test_get_task_and_cancel_task():
    respx.get(CARD_URL).respond(json={})
    methods = []

    def _capture(request):
        body = _rpc(request)
        methods.append((body["method"], body["params"].get("id")))
        state = "canceled" if body["method"] == "tasks/cancel" else "working"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": {
            "id": "t5", "contextId": "c5", "status": {"state": state}}})

    respx.post(RPC_URL).mock(side_effect=_capture)
    c = _client()
    got = await c.get_task("t5")
    assert got.task_id == "t5" and got.state == "working" and not got.is_terminal
    canceled = await c.cancel_task("t5")
    assert canceled.is_terminal and canceled.state == "canceled"
    assert methods == [("tasks/get", "t5"), ("tasks/cancel", "t5")]


@pytest.mark.asyncio
@respx.mock
async def test_poll_until_terminal_polls_until_completed(monkeypatch):
    respx.get(CARD_URL).respond(json={})
    # working, working, completed
    states = iter(["working", "working", "completed"])

    def _capture(request):
        st = next(states)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": {
            "id": "t7", "contextId": "c7", "status": {"state": st},
            "artifacts": [{"parts": [{"kind": "text", "text": "final"}]}]}})

    respx.post(RPC_URL).mock(side_effect=_capture)

    async def _no_sleep(_):  # don't actually wait in the test
        return None

    monkeypatch.setattr("a2a.client.asyncio.sleep", _no_sleep)
    res = await _client().poll_until_terminal("t7", interval=0.01, max_wait=5)
    assert res.is_terminal and res.state == "completed" and res.text == "final"


@pytest.mark.asyncio
@respx.mock
async def test_poll_stops_on_input_required(monkeypatch):
    respx.get(CARD_URL).respond(json={})
    states = iter(["working", "input-required"])

    def _capture(request):
        st = next(states)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": {
            "id": "t8", "contextId": "c8", "status": {"state": st}}})

    respx.post(RPC_URL).mock(side_effect=_capture)
    monkeypatch.setattr("a2a.client.asyncio.sleep", lambda _: _async_none())
    res = await _client().poll_until_terminal("t8", interval=0.01, max_wait=5)
    assert res.input_required and not res.is_terminal


async def _async_none():
    return None
