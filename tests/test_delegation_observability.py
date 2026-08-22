"""Tests for the delegation-path observability chokepoint (#683):
dispatch() per-status counters + span wrapping in agent/delegates.py,
the A2AAdapter ``delegate.dispatch`` span shell, W3C traceparent
propagation, and the per-request trace-header hook in a2a_outbound.py."""

import asyncio
import time

import httpx
import pytest

import agent.delegate_adapters as adapters
from a2a_outbound import (
    A2ADispatchError,
    A2ADispatchTimeout,
    A2AResult,
    _stamp_trace_headers,
)
from agent import metrics
from agent.delegates import Delegate, DelegateError, dispatch


class _StubAdapter:
    type = "obs-stub"

    def __init__(self) -> None:
        self.raise_exc: BaseException | None = None

    async def dispatch(self, delegate, query, *, timeout=60.0,
                       progress_callback=None, push_notification_url=None,
                       push_notification_token=None) -> str:
        if self.raise_exc is not None:
            raise self.raise_exc
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
    before = metrics.snapshot()["counters"]
    result = await dispatch(_delegate(), "hello")
    assert result == "echo: hello"
    counters = metrics.snapshot()["counters"]
    assert counters["delegate_dispatch_total"] \
        == before.get("delegate_dispatch_total", 0) + 1
    assert counters.get("delegate_dispatch_total:stubby", 0) >= 1
    assert counters["delegate_dispatch:ok"] \
        == before.get("delegate_dispatch:ok", 0) + 1
    assert counters.get("delegate_dispatch:stubby:ok", 0) >= 1


@pytest.mark.asyncio
async def test_dispatch_success_writes_latency_gauge(stub):
    await dispatch(_delegate(), "hello")
    gauges = metrics.snapshot()["gauges"]
    assert isinstance(gauges["delegate_dispatch_latency_ms"], int)
    assert isinstance(gauges["delegate_dispatch_latency_ms:stubby"], int)


@pytest.mark.asyncio
async def test_dispatch_failure_increments_error_counter_and_raises(stub):
    stub.raise_exc = DelegateError("boom")
    before = metrics.snapshot()["counters"]
    with pytest.raises(DelegateError):
        await dispatch(_delegate(), "hello")
    counters = metrics.snapshot()["counters"]
    assert counters["delegate_dispatch_errors"] \
        == before.get("delegate_dispatch_errors", 0) + 1
    assert counters.get("delegate_dispatch_errors:stubby", 0) >= 1
    assert counters["delegate_dispatch:error"] \
        == before.get("delegate_dispatch:error", 0) + 1


@pytest.mark.asyncio
async def test_dispatch_timeout_counts_as_timeout_status(stub):
    stub.raise_exc = A2ADispatchTimeout("stubby: no response within 1s")
    before = metrics.snapshot()["counters"]
    with pytest.raises(A2ADispatchTimeout):
        await dispatch(_delegate(), "hello")
    counters = metrics.snapshot()["counters"]
    assert counters["delegate_dispatch:timeout"] \
        == before.get("delegate_dispatch:timeout", 0) + 1
    assert counters.get("delegate_dispatch:stubby:timeout", 0) >= 1
    # Compat alias: a timeout is a raised dispatch → still an error.
    assert counters["delegate_dispatch_errors"] \
        == before.get("delegate_dispatch_errors", 0) + 1


@pytest.mark.asyncio
async def test_cancelled_dispatch_never_writes_latency_gauge(stub):
    # Seed the gauge with a known "last completed round-trip" reading.
    metrics.set("delegate_dispatch_latency_ms", 4242)
    metrics.set("delegate_dispatch_latency_ms:stubby", 4242)
    before = metrics.snapshot()["counters"]
    stub.raise_exc = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await dispatch(_delegate(), "hello")
    snap = metrics.snapshot()
    # A barge-in abort lands in well under a second — it must NOT clobber
    # the last meaningful round-trip latency (pre-#683, CancelledError
    # bypassed the counting path entirely).
    assert snap["gauges"]["delegate_dispatch_latency_ms"] == 4242
    assert snap["gauges"]["delegate_dispatch_latency_ms:stubby"] == 4242
    counters = snap["counters"]
    assert counters["delegate_dispatch:cancelled"] \
        == before.get("delegate_dispatch:cancelled", 0) + 1
    assert counters["delegate_barge_drop_total"] \
        == before.get("delegate_barge_drop_total", 0) + 1
    assert counters.get("delegate_barge_drop_total:stubby", 0) >= 1
    # Compat aliases keep their original semantics: a cancel is neither
    # a success nor an error.
    assert counters.get("delegate_dispatch_total", 0) \
        == before.get("delegate_dispatch_total", 0)
    assert counters.get("delegate_dispatch_errors", 0) \
        == before.get("delegate_dispatch_errors", 0)


@pytest.mark.asyncio
async def test_unknown_type_still_raises_delegate_error():
    with pytest.raises(DelegateError):
        await dispatch(Delegate(name="x", description="", type="nope"), "q")


def test_dispatch_timeout_is_a_dispatch_error():
    # Callers that catch A2ADispatchError must be unaffected by the
    # distinct timeout type.
    assert issubclass(A2ADispatchTimeout, A2ADispatchError)


# --- A2AAdapter delegate.dispatch span shell --------------------------------


class _FakeSpan:
    def __init__(self, kwargs):
        self.open_kwargs = kwargs
        self.updates: list[dict] = []
        self.ended = False

    def update(self, **kw):
        self.updates.append(kw)
        return self

    def end(self, **_kw):
        self.ended = True


class _FakeTraceRoot:
    def __init__(self):
        self.spans: list[_FakeSpan] = []

    def start_observation(self, **kw):
        sp = _FakeSpan(kw)
        self.spans.append(sp)
        return sp


class _FakeA2AClient:
    def __init__(self, raise_exc: BaseException | None = None):
        self.raise_exc = raise_exc

    async def supports_streaming(self) -> bool:
        return False

    async def send(self, query, **_kw) -> A2AResult:
        if self.raise_exc is not None:
            raise self.raise_exc
        return A2AResult(text="answer", state="completed",
                         task_id="t1", context_id="c1")


def _a2a_delegate() -> Delegate:
    # Non-loopback url so _default_push_url stays None (no callback wiring).
    return Delegate(name="hub", description="fleet hub", type="a2a",
                    url="http://198.51.100.7:1/a2a")


@pytest.fixture
def a2a(monkeypatch):
    """An A2AAdapter with a fake client and a captured trace root."""
    monkeypatch.setenv("A2A_STREAM", "0")
    adapter = adapters.A2AAdapter()
    client = _FakeA2AClient()
    monkeypatch.setattr(adapter, "client_for", lambda d: client)
    root = _FakeTraceRoot()
    monkeypatch.setattr(adapters, "active_trace", lambda user_id=None: root)
    return adapter, client, root


@pytest.mark.asyncio
async def test_a2a_dispatch_opens_and_closes_span_on_success(a2a):
    adapter, _client, root = a2a
    result = await adapter.dispatch(_a2a_delegate(), "hello")
    assert result == "answer"
    assert len(root.spans) == 1
    sp = root.spans[0]
    assert sp.open_kwargs["name"] == "delegate.dispatch"
    assert sp.open_kwargs["input"] == {"delegate": "hub", "query_len": 5}
    assert sp.ended
    assert {"output": {"chars": len("answer")}} in sp.updates


@pytest.mark.asyncio
async def test_a2a_dispatch_span_records_error(a2a):
    adapter, client, root = a2a
    client.raise_exc = A2ADispatchError("hub: send failed: boom")
    with pytest.raises(A2ADispatchError):
        await adapter.dispatch(_a2a_delegate(), "hello")
    sp = root.spans[0]
    assert sp.ended
    assert any(
        u.get("level") == "ERROR" and "boom" in u.get("status_message", "")
        for u in sp.updates
    )


@pytest.mark.asyncio
async def test_a2a_dispatch_span_marks_cancel_and_reraises(a2a):
    adapter, client, root = a2a
    client.raise_exc = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await adapter.dispatch(_a2a_delegate(), "hello")
    sp = root.spans[0]
    assert sp.ended
    assert any(u.get("status_message") == "cancelled" for u in sp.updates)


@pytest.mark.asyncio
async def test_a2a_dispatch_works_with_tracing_off(monkeypatch):
    # No Langfuse env / no live trace → active_trace() is the null span;
    # the shell must be a pure no-op around a working dispatch.
    monkeypatch.setenv("A2A_STREAM", "0")
    adapter = adapters.A2AAdapter()
    monkeypatch.setattr(adapter, "client_for", lambda d: _FakeA2AClient())
    assert await adapter.dispatch(_a2a_delegate(), "hello") == "answer"


# --- W3C traceparent propagation --------------------------------------------


class _HexTrace:
    trace_id = "0af7651916cd43dd8448eb211c80319c"
    _orbis_session_id = "sess-9"
    id = "b7ad6b7169203331"


def test_propagation_headers_emit_w3c_traceparent(monkeypatch):
    from agent import tracing

    monkeypatch.setattr(tracing, "enabled", lambda: True)
    h = tracing.propagation_headers(trace=_HexTrace())
    assert h["traceparent"] == (
        "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    )
    assert h["Langfuse-Trace-Id"] == "0af7651916cd43dd8448eb211c80319c"
    assert h["Langfuse-Session-Id"] == "sess-9"


def test_propagation_headers_withhold_malformed_traceparent(monkeypatch):
    from agent import tracing

    class _T:
        trace_id = "tr-123"  # not W3C hex
        _orbis_session_id = "sess-9"
        id = "obs-1"

    monkeypatch.setattr(tracing, "enabled", lambda: True)
    h = tracing.propagation_headers(trace=_T())
    # Langfuse-* still flow; a malformed traceparent is worse than none.
    assert "traceparent" not in h
    assert h["Langfuse-Trace-Id"] == "tr-123"


@pytest.mark.asyncio
async def test_trace_header_hook_noop_when_tracing_off():
    # With no Langfuse configured / no live trace, the hook must leave the
    # request untouched and never raise.
    req = httpx.Request("POST", "http://127.0.0.1:9/a2a")
    await _stamp_trace_headers(req)
    assert not any(k.lower().startswith("langfuse") for k in req.headers)
    assert "traceparent" not in req.headers


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


@pytest.mark.asyncio
async def test_trace_header_hook_stamps_traceparent(monkeypatch):
    from agent import tracing

    monkeypatch.setattr(tracing, "enabled", lambda: True)
    monkeypatch.setattr(tracing, "active_trace", lambda user_id=None: _HexTrace())
    req = httpx.Request("POST", "http://127.0.0.1:9/a2a")
    await _stamp_trace_headers(req)
    assert req.headers["traceparent"] == (
        "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    )


# --- delegate ask timeouts --------------------------------------------------


def test_expired_delegate_ask_counts_timeout():
    import agent.user_state as us
    from agent.user_state import (
        DELEGATE_ASK_TTL_SECS,
        DelegateAsk,
        register_delegate_ask_on_active,
        take_oldest_delegate_ask,
    )

    st = us.user_state_for("obs-ask-user")
    st.active_delivery = object()  # reads as an active session
    try:
        before = metrics.snapshot()["counters"]
        assert register_delegate_ask_on_active(DelegateAsk(
            task_id="stale-obs", delegate="obs-hub", question="q",
            context_id=None,
            created_at=time.time() - DELEGATE_ASK_TTL_SECS - 5,
        ))
        while take_oldest_delegate_ask() is not None:
            pass
        counters = metrics.snapshot()["counters"]
        assert counters.get("delegate_ask_timeout_total", 0) \
            == before.get("delegate_ask_timeout_total", 0) + 1
        assert counters.get("delegate_ask_timeout_total:obs-hub", 0) \
            == before.get("delegate_ask_timeout_total:obs-hub", 0) + 1
    finally:
        st.pending_delegate_asks.clear()
        st.active_delivery = None
        us._REGISTRY.drop("obs-ask-user")
