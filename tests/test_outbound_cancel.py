"""Tests for verbal cancel of delegated work (#681):
cancel_latest_outbound + A2AClient.cancel."""

from __future__ import annotations

import pytest

import agent.delegate_adapters as adapters
import agent.user_state as us
from a2a_outbound import A2AClient, A2ADispatchError
from agent.delegates import Delegate
from agent.outbound_cancel import cancel_latest_outbound
from agent.user_state import DelegateAsk, register_delegate_ask_on_active
from memory import Memory


class _Delivery:
    def __init__(self):
        self.spoken: list[tuple[str, str]] = []

    async def deliver(self, text, *, priority=None, source=""):
        self.spoken.append((source, text))


@pytest.fixture
def mem(tmp_path, monkeypatch):
    m = Memory(tmp_path / "t.sqlite")
    monkeypatch.setattr(adapters, "_MEMORY_PROVIDER", lambda: m)
    yield m
    m.close()


@pytest.fixture
def active_state():
    st = us.user_state_for("cancel-test-user")
    st.active_delivery = _Delivery()
    st.pending_delegate_asks.clear()
    yield st
    st.pending_delegate_asks.clear()
    st.active_delivery = None


class _Registry:
    def get(self, name):
        if name == "hub":
            return Delegate(name="hub", description="d", type="a2a",
                            url="http://hub:1/a2a")
        return None


class _FakeClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.cancelled: list[str] = []

    async def cancel(self, task_id):
        if self.fail:
            raise A2ADispatchError("hub: tasks/cancel failed: nope")
        self.cancelled.append(task_id)
        return "canceled"


@pytest.mark.asyncio
async def test_cancels_newest_live_task(mem, active_state, monkeypatch):
    mem.outbound.record(task_id="old", delegate="hub", query="q1")
    mem.outbound.record(task_id="new", delegate="hub", query="q2")
    client = _FakeClient()
    monkeypatch.setattr(
        adapters.get_adapter("a2a"), "client_for", lambda _d: client
    )
    name = await cancel_latest_outbound(_Registry())
    assert name == "hub"
    assert client.cancelled == ["new"]
    assert mem.outbound.get("new")["status"] == "canceled"
    assert mem.outbound.get("old")["status"] == "submitted"  # untouched
    assert active_state.active_delivery.spoken
    assert "stop" in active_state.active_delivery.spoken[0][1].lower()


@pytest.mark.asyncio
async def test_noop_when_nothing_live(mem, active_state):
    assert await cancel_latest_outbound(_Registry()) is None
    assert not active_state.active_delivery.spoken


@pytest.mark.asyncio
async def test_local_cancel_wins_when_remote_fails(mem, active_state, monkeypatch):
    mem.outbound.record(task_id="t-1", delegate="hub", query="q")
    monkeypatch.setattr(
        adapters.get_adapter("a2a"), "client_for",
        lambda _d: _FakeClient(fail=True),
    )
    name = await cancel_latest_outbound(_Registry())
    assert name == "hub"
    # Local row canceled regardless — the requery must never re-deliver this.
    assert mem.outbound.get("t-1")["status"] == "canceled"
    assert "didn't confirm" in active_state.active_delivery.spoken[0][1]


@pytest.mark.asyncio
async def test_cancel_clears_pending_ask(mem, active_state, monkeypatch):
    import time as _t
    mem.outbound.record(task_id="t-1", delegate="hub", query="q",
                        status="input-required")
    register_delegate_ask_on_active(DelegateAsk(
        task_id="t-1", delegate="hub", question="which one?",
        context_id=None, created_at=_t.time(),
    ))
    monkeypatch.setattr(
        adapters.get_adapter("a2a"), "client_for", lambda _d: _FakeClient()
    )
    await cancel_latest_outbound(_Registry())
    assert us.take_oldest_delegate_ask() is None  # ask is gone


# --- A2AClient.cancel --------------------------------------------------------


@pytest.mark.asyncio
async def test_client_cancel_maps_state(monkeypatch):
    from a2a.types import Task, TaskState

    client = A2AClient(url="http://hub:7871/a2a", name="hub")

    class _Sdk:
        async def cancel_task(self, request):
            assert request.id == "t-9"
            t = Task()
            t.id = "t-9"
            t.status.state = TaskState.TASK_STATE_CANCELED
            return t

    async def _fake_ensure():
        return _Sdk()

    monkeypatch.setattr(client, "_ensure_client", _fake_ensure)
    assert await client.cancel("t-9") == "canceled"


@pytest.mark.asyncio
async def test_client_cancel_failure_raises(monkeypatch):
    client = A2AClient(url="http://hub:7871/a2a", name="hub")

    class _Sdk:
        async def cancel_task(self, _request):
            raise RuntimeError("unsupported")

    async def _fake_ensure():
        return _Sdk()

    monkeypatch.setattr(client, "_ensure_client", _fake_ensure)
    with pytest.raises(A2ADispatchError, match="tasks/cancel failed"):
        await client.cancel("t-9")
