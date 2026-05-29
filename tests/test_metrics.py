"""Tests for agent.metrics — the in-process counter module that
silent-failure paths report through.

The module is deliberately tiny (counters dict + lock + snapshot), so
the tests just nail down the contract: inc() accumulates, set() is
last-write-wins, snapshot() returns a copy that callers can mutate
without poisoning the source, reset() clears.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from agent import metrics
from agent.personality import analyze_session_drift


def setup_function() -> None:
    metrics.reset()


def test_inc_default_is_one():
    metrics.inc("foo")
    metrics.inc("foo")
    assert metrics.snapshot()["counters"]["foo"] == 2


def test_inc_by_n():
    metrics.inc("bar", by=5)
    metrics.inc("bar", by=3)
    assert metrics.snapshot()["counters"]["bar"] == 8


def test_inc_by_zero_is_noop():
    metrics.inc("baz", by=0)
    assert "baz" not in metrics.snapshot()["counters"]


def test_set_is_last_write_wins():
    metrics.set("queue_depth", 7)
    metrics.set("queue_depth", 3)
    assert metrics.snapshot()["gauges"]["queue_depth"] == 3


def test_snapshot_returns_independent_copy():
    metrics.inc("alpha")
    snap = metrics.snapshot()
    snap["counters"]["alpha"] = 999
    snap["counters"]["new_key"] = 1
    # Mutating the snapshot must not bleed back into the live state.
    fresh = metrics.snapshot()
    assert fresh["counters"]["alpha"] == 1
    assert "new_key" not in fresh["counters"]


def test_reset_clears_both_buckets():
    metrics.inc("counter")
    metrics.set("gauge", "value")
    metrics.reset()
    snap = metrics.snapshot()
    assert snap["counters"] == {}
    assert snap["gauges"] == {}


def test_unknown_counter_absent_until_inc():
    """Empty state should have no key for a never-bumped counter so
    /api/metrics doesn't carry every possible counter name on a quiet
    process."""
    snap = metrics.snapshot()
    assert "never_seen" not in snap["counters"]


def test_personality_drift_call_failure_increments_metric(monkeypatch):
    class _Completions:
        async def create(self, **_kwargs):
            raise RuntimeError("boom")

    class _AsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=_AsyncOpenAI))
    out = asyncio.run(analyze_session_drift(
        [{"role": "user", "content": "hello"}],
        llm_url="http://test/v1",
        model="test",
        api_key="test",
    ))
    assert out == []
    assert metrics.snapshot()["counters"]["drift_llm_call_failed"] == 1


def test_personality_drift_empty_response_increments_metric(monkeypatch):
    class _Completions:
        async def create(self, **_kwargs):
            return SimpleNamespace(choices=[])

    class _AsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=_AsyncOpenAI))
    out = asyncio.run(analyze_session_drift(
        [{"role": "user", "content": "hello"}],
        llm_url="http://test/v1",
        model="test",
        api_key="test",
    ))
    assert out == []
    assert metrics.snapshot()["counters"]["drift_llm_empty_response"] == 1


def test_personality_drift_json_parse_failure_increments_metric(monkeypatch):
    class _Completions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="not-json"),
                    ),
                ],
            )

    class _AsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=_AsyncOpenAI))
    out = asyncio.run(analyze_session_drift(
        [{"role": "user", "content": "hello"}],
        llm_url="http://test/v1",
        model="test",
        api_key="test",
    ))
    assert out == []
    assert metrics.snapshot()["counters"]["drift_json_parse_failed"] == 1
