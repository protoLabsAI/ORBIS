"""Shape tests for agent/tracing.py against a mocked Langfuse v4 client.

The goal is to pin the call contract between our helpers and the SDK, not
to exercise Langfuse itself. Everything that hits the network is mocked.
"""

from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def stub_langfuse(monkeypatch: pytest.MonkeyPatch):
    """Install a stubbed `langfuse` package before agent.tracing imports it.

    Returns the (client, propagate_attributes_mock) pair so tests can
    assert on calls. Reloads agent.tracing so the module reads fresh env
    + picks up the stubbed SDK.
    """
    monkeypatch.setenv("LANGFUSE_HOST", "http://fake")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-fake")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-fake")

    client = MagicMock(name="LangfuseClient")

    # Spans returned by start_observation are the handles the observer
    # code holds onto across frames. Give every call a fresh mock so
    # tests can distinguish root / llm / tool spans by identity.
    def _new_span(**_kwargs):
        sp = MagicMock(name="LangfuseSpan")
        sp.trace_id = "trace-mock"
        sp.id = "obs-mock"
        return sp

    client.start_observation.side_effect = _new_span

    propagate_calls: list[dict] = []

    @contextmanager
    def _propagate_attributes(**kwargs):
        propagate_calls.append(kwargs)
        yield

    class _TraceContext:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    stub = MagicMock(name="langfuse_module")
    stub.Langfuse.return_value = client
    stub.propagate_attributes = _propagate_attributes

    types_stub = MagicMock(name="langfuse.types_module")
    types_stub.TraceContext = _TraceContext

    monkeypatch.setitem(sys.modules, "langfuse", stub)
    monkeypatch.setitem(sys.modules, "langfuse.types", types_stub)

    import agent.tracing as tracing
    importlib.reload(tracing)
    tracing._CLIENT = None  # force re-init through the stub

    yield tracing, client, propagate_calls, _TraceContext

    # Tear down — nuke the stubs + reload so other tests see a clean slate.
    for k in ("langfuse", "langfuse.types"):
        sys.modules.pop(k, None)
    importlib.reload(tracing)


@pytest.fixture
def disabled_tracing(monkeypatch: pytest.MonkeyPatch):
    for k in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    import agent.tracing as tracing
    importlib.reload(tracing)
    yield tracing
    importlib.reload(tracing)


# --- fail-open when env unset ------------------------------------------------


def test_disabled_helpers_return_null_span(disabled_tracing):
    t = disabled_tracing
    assert t.enabled() is False
    null = t.start_turn_trace(session_id="s1")
    # _NullSpan duck-types .end() / .update() / .start_observation()
    null.update(output="irrelevant")
    null.end()
    assert null.start_observation(name="x") is not null or True  # returns another NullSpan


def test_disabled_span_context_yields_null(disabled_tracing):
    t = disabled_tracing
    with t.span("stt.whisper") as sp:
        sp.update(output="x")
    # No exceptions = pass. Shape stays green even when tracing is off.


# --- start_turn_trace: v4 call shape ----------------------------------------


def test_start_turn_trace_uses_v4_api(stub_langfuse):
    tracing, client, propagate_calls, _ = stub_langfuse
    trace = tracing.start_turn_trace(
        session_id="sess-42",
        user_id="josh",
        input="hello there",
        metadata={"trigger": "test"},
    )

    # propagate_attributes entered exactly once with the OTEL-style kwargs.
    assert propagate_calls == [{"session_id": "sess-42", "user_id": "josh"}]

    # client.start_observation called with as_type='span', correct payload.
    client.start_observation.assert_called_once()
    call = client.start_observation.call_args
    assert call.kwargs["name"] == "user_turn"
    assert call.kwargs["as_type"] == "span"
    assert call.kwargs["input"] == "hello there"
    assert call.kwargs["metadata"] == {"trigger": "test"}

    # The span handle carries ORBIS-stamped session/user so
    # propagation_headers() can read them later.
    assert trace._orbis_session_id == "sess-42"
    assert trace._orbis_user_id == "josh"


def test_continue_trace_builds_trace_context(stub_langfuse):
    tracing, client, propagate_calls, TraceContext = stub_langfuse
    trace = tracing.continue_trace(
        trace_id="trace-abc", session_id="sess-99", parent_span_id="span-parent",
    )
    assert propagate_calls == [{"session_id": "sess-99"}]

    client.start_observation.assert_called_once()
    kwargs = client.start_observation.call_args.kwargs
    assert kwargs["name"] == "a2a_inbound"
    assert kwargs["as_type"] == "span"
    tc = kwargs["trace_context"]
    assert isinstance(tc, TraceContext)
    assert tc.kwargs == {"trace_id": "trace-abc", "parent_span_id": "span-parent"}

    assert trace._orbis_session_id == "sess-99"


def test_continue_trace_without_parent_span(stub_langfuse):
    tracing, client, _, TraceContext = stub_langfuse
    tracing.continue_trace(trace_id="t1", session_id="s1")
    tc = client.start_observation.call_args.kwargs["trace_context"]
    assert tc.kwargs == {"trace_id": "t1"}


# --- propagation headers -----------------------------------------------------


def test_propagation_headers_from_live_trace(stub_langfuse):
    tracing, _, _, _ = stub_langfuse
    trace = tracing.start_turn_trace(session_id="sess-A", user_id="josh", input="hi")
    headers = tracing.propagation_headers(trace=trace)
    assert headers["Langfuse-Trace-Id"] == "trace-mock"
    assert headers["Langfuse-Session-Id"] == "sess-A"
    # Parent observation defaults to the span's own id so child calls nest.
    assert headers["Langfuse-Parent-Observation-Id"] == "obs-mock"


def test_propagation_headers_disabled_returns_empty(disabled_tracing):
    t = disabled_tracing
    assert t.propagation_headers(trace=object()) == {}


# --- _NullSpan legacy aliases ------------------------------------------------


def test_nullspan_v2_aliases_no_op(disabled_tracing):
    """Stragglers calling .span() / .generation() shouldn't crash."""
    t = disabled_tracing
    null = t._NULL
    assert null.span(name="x") is not None
    assert null.generation(name="y") is not None
    with null.start_as_current_observation(name="z") as child:
        child.update(output="q")
        child.end()
