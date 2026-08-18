"""Tests for the delegation-path observability chokepoint (#683):
dispatch() counters + span wrapping in agent/delegates.py, and the
per-request trace-header hook in a2a_outbound.py."""

import httpx
import pytest

import agent.delegate_adapters as adapters
from agent import metrics
from agent.delegates import Delegate, DelegateError, dispatch
from a2a_outbound import _stamp_trace_headers


class _StubAdapter:
    type = "obs-stub"

    def __init__(self) -> None:
        self.fail = False

    async def dispatch(self, delegate, query, *, timeout=60.0,
                       progress_callback=None, push_notification_url=None,
                       push_notification_token=None) -> str:
        if self.fail:
            raise DelegateError("boom")
        return f"echo: {query}"


@pytest.fixture
def stub(monkeypatch):
    """Route dispatch()'s adapter lookup to a stub without touching the
    real adapter registry (registering there leaks into
    delegate_type_specs / builtin-type tests)."""
    stub = _StubAdapter()
    real = adapters.get_adapter

    def _get(dtype):
        if dtype == "obs-stub":
            return stub
        return real(dtype)

    monkeypatch.setattr(adapters, "get_adapter", _get)
    return stub


def _delegate() -> Delegate:
    return Delegate(name="stubby", description="test", type="obs-stub")


@pytest.mark.asyncio
async def test_dispatch_success_increments_counters(stub):
    before = metrics.snapshot()["counters"].get("delegate_dispatch_total", 0)
    result = await dispatch(_delegate(), "hello")
    assert result == "echo: hello"
    counters = metrics.snapshot()["counters"]
    assert counters["delegate_dispatch_total"] == before + 1
    assert counters.get("delegate_dispatch_total:stubby", 0) >= 1


@pytest.mark.asyncio
async def test_dispatch_failure_increments_error_counter_and_raises(stub):
    stub.fail = True
    before = metrics.snapshot()["counters"].get("delegate_dispatch_errors", 0)
    with pytest.raises(DelegateError):
        await dispatch(_delegate(), "hello")
    counters = metrics.snapshot()["counters"]
    assert counters["delegate_dispatch_errors"] == before + 1
    assert counters.get("delegate_dispatch_errors:stubby", 0) >= 1


@pytest.mark.asyncio
async def test_unknown_type_still_raises_delegate_error():
    with pytest.raises(DelegateError):
        await dispatch(Delegate(name="x", description="", type="nope"), "q")


@pytest.mark.asyncio
async def test_trace_header_hook_noop_when_tracing_off():
    # With no Langfuse configured / no live trace, the hook must leave the
    # request untouched and never raise.
    req = httpx.Request("POST", "http://127.0.0.1:9/a2a")
    await _stamp_trace_headers(req)
    assert not any(k.lower().startswith("langfuse") for k in req.headers)


@pytest.mark.asyncio
async def test_trace_header_hook_stamps_live_trace(monkeypatch):
    from agent import tracing

    class _FakeTrace:
        trace_id = "tr-123"
        _orbis_session_id = "sess-9"
        id = "obs-1"

    monkeypatch.setattr(tracing, "enabled", lambda: True)
    monkeypatch.setattr(tracing, "active_trace", lambda user_id=None: _FakeTrace())
    req = httpx.Request("POST", "http://127.0.0.1:9/a2a")
    await _stamp_trace_headers(req)
    assert req.headers["Langfuse-Trace-Id"] == "tr-123"
    assert req.headers["Langfuse-Session-Id"] == "sess-9"
