"""Tests for delegate_async — fire-and-forget delegation (orbis-1s0).

It must ack immediately (so the conversation keeps flowing) and surface
the delegate's answer through the DeliveryController when it lands, rather
than blocking the turn like delegate_to.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import agent.tools as tools
from agent.delegates import DelegateError
from agent.delivery import Priority
from agent.tools import (
    ASYNC_TOOL_NAMES,
    _BG_DELEGATE_TASKS,
    _delegate_async_handler,
    register_tools,
)


class FakeParams:
    def __init__(self, arguments):
        self.arguments = arguments
        self.results: list[str] = []

    async def result_callback(self, result: str) -> None:
        self.results.append(result)


class FakeDelivery:
    def __init__(self):
        self.delivered: list[tuple] = []

    async def deliver(self, phrase, *, priority=None, source=None, **kw):
        self.delivered.append((phrase, priority, source))


class FakeRegistry:
    def __init__(self, delegate):
        self._d = delegate

    def get(self, name):
        return self._d if name == self._d.name else None

    def names(self):
        return [self._d.name]

    def all(self):
        return [self._d]


class FakeLLM:
    def __init__(self):
        self.registered: dict[str, bool] = {}  # name -> cancel_on_interruption

    def register_function(self, name, handler, cancel_on_interruption=True):
        self.registered[name] = cancel_on_interruption


def _delegate():
    return SimpleNamespace(name="ava", type="a2a", description="chief of staff")


def test_delegate_async_is_recognized_as_async() -> None:
    assert "delegate_async" in ASYNC_TOOL_NAMES
    assert "delegate_async" in list(ASYNC_TOOL_NAMES)


@pytest.mark.asyncio
async def test_acks_immediately_then_delivers_result(monkeypatch) -> None:
    registry = FakeRegistry(_delegate())
    delivery = FakeDelivery()

    async def fake_dispatch(d, query, *, timeout, **kw):
        assert query == "do the thing"
        return "All done — shipped it."

    monkeypatch.setattr(tools, "delegate_dispatch", fake_dispatch)

    handler = _delegate_async_handler(registry, delivery=delivery)
    params = FakeParams({"target": "ava", "query": "do the thing"})
    await handler(params)

    # Immediate ack — turn doesn't block.
    assert len(params.results) == 1
    assert "ava" in params.results[0].lower()
    assert not delivery.delivered  # nothing delivered yet

    # Background task delivers the real answer as TIME_SENSITIVE.
    await asyncio.gather(*list(_BG_DELEGATE_TASKS))
    assert len(delivery.delivered) == 1
    phrase, priority, source = delivery.delivered[0]
    assert phrase == "All done — shipped it."
    assert priority == Priority.TIME_SENSITIVE
    assert source == "ava"


@pytest.mark.asyncio
async def test_delegate_error_is_delivered_not_swallowed(monkeypatch) -> None:
    registry = FakeRegistry(_delegate())
    delivery = FakeDelivery()

    async def boom(d, query, *, timeout, **kw):
        raise DelegateError("connection refused")

    monkeypatch.setattr(tools, "delegate_dispatch", boom)

    handler = _delegate_async_handler(registry, delivery=delivery)
    params = FakeParams({"target": "ava", "query": "x"})
    await handler(params)
    await asyncio.gather(*list(_BG_DELEGATE_TASKS))

    assert len(delivery.delivered) == 1
    phrase, priority, source = delivery.delivered[0]
    assert priority == Priority.TIME_SENSITIVE
    assert source == "ava"


@pytest.mark.asyncio
async def test_missing_args_acks_without_dispatch(monkeypatch) -> None:
    registry = FakeRegistry(_delegate())
    delivery = FakeDelivery()
    handler = _delegate_async_handler(registry, delivery=delivery)
    params = FakeParams({"target": "ava"})  # no query
    await handler(params)
    assert params.results and "need" in params.results[0].lower()
    assert not delivery.delivered


def test_registered_only_when_delivery_present() -> None:
    registry = FakeRegistry(_delegate())

    with_delivery = FakeLLM()
    register_tools(with_delivery, delegates=registry, delivery=FakeDelivery())
    assert "delegate_to" in with_delivery.registered
    assert "delegate_async" in with_delivery.registered
    # async tool must not be cancelled on barge-in
    assert with_delivery.registered["delegate_async"] is False

    without = FakeLLM()
    register_tools(without, delegates=registry, delivery=None)
    assert "delegate_to" in without.registered
    assert "delegate_async" not in without.registered


# --- proactive follow-up nudge on slow delegations (orbis-29q) --------------


@pytest.mark.asyncio
async def test_slow_delegation_nudges_then_delivers(monkeypatch) -> None:
    import asyncio
    monkeypatch.setenv("DELEGATE_NUDGE_SECS", "0.05")
    registry = FakeRegistry(_delegate())
    delivery = FakeDelivery()

    async def slow(d, query, *, timeout, **kw):
        await asyncio.sleep(0.2)  # longer than the nudge window
        return "all done"

    monkeypatch.setattr(tools, "delegate_dispatch", slow)
    handler = _delegate_async_handler(registry, delivery=delivery)
    await handler(FakeParams({"target": "ava", "query": "the big job"}))
    await asyncio.gather(*list(_BG_DELEGATE_TASKS))

    phrases = [c[0] for c in delivery.delivered]
    assert any("still waiting" in p for p in phrases)   # the nudge fired
    assert any("all done" in p for p in phrases)        # then the real result


@pytest.mark.asyncio
async def test_fast_delegation_does_not_nudge(monkeypatch) -> None:
    import asyncio
    monkeypatch.setenv("DELEGATE_NUDGE_SECS", "0.3")
    registry = FakeRegistry(_delegate())
    delivery = FakeDelivery()

    async def fast(d, query, *, timeout, **kw):
        return "quick result"

    monkeypatch.setattr(tools, "delegate_dispatch", fast)
    handler = _delegate_async_handler(registry, delivery=delivery)
    await handler(FakeParams({"target": "ava", "query": "x"}))
    await asyncio.gather(*list(_BG_DELEGATE_TASKS))
    await asyncio.sleep(0.05)  # let any stray nudge settle (it shouldn't fire)

    phrases = [c[0] for c in delivery.delivered]
    assert not any("still waiting" in p for p in phrases)  # nudge was cancelled
    assert phrases == ["quick result"]


# --- delegate_to no longer blocks the voice turn on A2A delegates -----------
# (the "ORBIS goes silent on a slow Ava round-trip" bug — A2A tasks are async,
# so delegate_to backgrounds them too and delivers via the DeliveryController.)


@pytest.mark.asyncio
async def test_delegate_to_a2a_is_nonblocking(monkeypatch) -> None:
    from agent.tools import _delegate_to_handler

    registry = FakeRegistry(_delegate())  # type="a2a"
    delivery = FakeDelivery()

    async def slow_dispatch(d, query, *, timeout, **kw):
        await asyncio.sleep(0.05)  # the turn must NOT wait for this
        return "Ava: the fleet is up."

    monkeypatch.setattr(tools, "delegate_dispatch", slow_dispatch)
    handler = _delegate_to_handler(registry, delivery=delivery)
    params = FakeParams({"target": "ava", "query": "who's online?"})
    await handler(params)

    # Returned immediately with an ack — did NOT hold the turn for the dispatch.
    assert len(params.results) == 1
    assert "ava" in params.results[0].lower()
    assert not delivery.delivered  # answer not in yet

    # The answer arrives via the DeliveryController when the bg task lands.
    await asyncio.gather(*list(_BG_DELEGATE_TASKS))
    phrases = [c[0] for c in delivery.delivered]
    assert "Ava: the fleet is up." in phrases


@pytest.mark.asyncio
async def test_delegate_to_acp_stays_synchronous(monkeypatch) -> None:
    from agent.tools import _delegate_to_handler

    registry = FakeRegistry(
        SimpleNamespace(name="codeBot", type="acp", description="coder")
    )
    delivery = FakeDelivery()

    async def fast_dispatch(d, query, **kw):
        return "Edited app.py."

    monkeypatch.setattr(tools, "delegate_dispatch", fast_dispatch)
    handler = _delegate_to_handler(registry, delivery=delivery)
    params = FakeParams({"target": "codeBot", "query": "fix it"})
    await handler(params)

    # Local agents relay synchronously: the answer is the turn's response, and
    # nothing routes through the async DeliveryController.
    assert params.results == ["Edited app.py."]
    assert not delivery.delivered
