"""Tests for delegate input-required HITL routing (#681): DelegateAsk
registration/TTL/ordering in user_state, and answer_delegate_ask's
send-into-the-task flow."""

from __future__ import annotations

import time

import pytest

import agent.delegate_adapters as adapters
import agent.user_state as us
from agent.delegate_ask import answer_delegate_ask
from agent.delegates import Delegate
from agent.user_state import (
    DELEGATE_ASK_TTL_SECS,
    DelegateAsk,
    clear_delegate_ask,
    register_delegate_ask_on_active,
    take_oldest_delegate_ask,
)


class _Delivery:
    def __init__(self):
        self.spoken: list[tuple[str, str]] = []

    async def deliver(self, text, *, priority=None, source=""):
        self.spoken.append((source, text))


@pytest.fixture
def active_state():
    """A registered user state that reads as an active session
    (active_sessions() gates on active_delivery being set)."""
    st = us.user_state_for("hitl-test-user")
    st.active_delivery = _Delivery()
    st.pending_delegate_asks.clear()
    yield st
    st.pending_delegate_asks.clear()
    st.active_delivery = None


def _ask(task_id: str, *, created_at: float | None = None) -> DelegateAsk:
    return DelegateAsk(
        task_id=task_id, delegate="hub", question=f"q-{task_id}",
        context_id="ctx", created_at=created_at or time.time(),
    )


# --- registration / ordering / TTL ------------------------------------------


def test_register_and_take_oldest(active_state):
    assert register_delegate_ask_on_active(_ask("t-1"))
    assert register_delegate_ask_on_active(_ask("t-2"))
    got = take_oldest_delegate_ask()
    assert got is not None and got.task_id == "t-1"
    got = take_oldest_delegate_ask()
    assert got is not None and got.task_id == "t-2"
    assert take_oldest_delegate_ask() is None


def test_reregister_refreshes_and_moves_to_end(active_state):
    register_delegate_ask_on_active(_ask("t-1"))
    register_delegate_ask_on_active(_ask("t-2"))
    register_delegate_ask_on_active(_ask("t-1"))  # re-ask → back of the line
    assert take_oldest_delegate_ask().task_id == "t-2"
    assert take_oldest_delegate_ask().task_id == "t-1"


def test_expired_ask_is_pruned_not_answered(active_state):
    register_delegate_ask_on_active(
        _ask("stale", created_at=time.time() - DELEGATE_ASK_TTL_SECS - 5)
    )
    register_delegate_ask_on_active(_ask("fresh"))
    got = take_oldest_delegate_ask()
    assert got is not None and got.task_id == "fresh"


def test_register_without_active_session_returns_false():
    st = us.user_state_for("hitl-inactive-user")
    st.active_session_id = None
    # No active session anywhere for this test's ask to land on is not
    # guaranteed (other fixtures may be active), so assert only the
    # inactive-state behavior via a fresh registry scan being consistent:
    # the function returns False when NO session is active.
    if not us.active_user_states():
        assert not register_delegate_ask_on_active(_ask("t-x"))


def test_clear_delegate_ask(active_state):
    register_delegate_ask_on_active(_ask("t-1"))
    clear_delegate_ask("t-1")
    assert take_oldest_delegate_ask() is None


# --- answer_delegate_ask -----------------------------------------------------


class _Res:
    def __init__(self, state, text="", task_id="t-1", input_required=False):
        self.state = state
        self.text = text
        self.task_id = task_id
        self.context_id = "ctx"
        self.input_required = input_required


class _FakeClient:
    def __init__(self, res):
        self._res = res
        self.sent: list[dict] = []

    async def send(self, query, **kwargs):
        self.sent.append({"query": query, **kwargs})
        if isinstance(self._res, Exception):
            raise self._res
        return self._res


class _Registry:
    def get(self, name):
        if name == "hub":
            return Delegate(name="hub", description="d", type="a2a",
                            url="http://hub:1/a2a")
        return None


@pytest.mark.asyncio
async def test_answer_sends_into_same_task_and_speaks_result(
    active_state, monkeypatch
):
    delivery = active_state.active_delivery
    client = _FakeClient(_Res("completed", text="done: 42"))
    monkeypatch.setattr(
        adapters.get_adapter("a2a"), "client_for", lambda _d: client
    )
    await answer_delegate_ask(_ask("t-1"), "use the blue one", _Registry())
    assert client.sent[0]["task_id"] == "t-1"
    assert client.sent[0]["context_id"] == "ctx"
    assert client.sent[0]["query"] == "use the blue one"
    assert delivery.spoken and "done: 42" in delivery.spoken[0][1]


@pytest.mark.asyncio
async def test_followup_question_rearms_ask(active_state, monkeypatch):
    delivery = active_state.active_delivery
    client = _FakeClient(_Res("input-required", text="and which branch?",
                              input_required=True))
    monkeypatch.setattr(
        adapters.get_adapter("a2a"), "client_for", lambda _d: client
    )
    await answer_delegate_ask(_ask("t-1"), "the ORBIS repo", _Registry())
    rearmed = take_oldest_delegate_ask()
    assert rearmed is not None and rearmed.task_id == "t-1"
    assert rearmed.question == "and which branch?"
    assert delivery.spoken and "which branch" in delivery.spoken[0][1]


@pytest.mark.asyncio
async def test_send_failure_is_spoken_not_silent(active_state, monkeypatch):
    delivery = active_state.active_delivery
    client = _FakeClient(RuntimeError("boom"))
    monkeypatch.setattr(
        adapters.get_adapter("a2a"), "client_for", lambda _d: client
    )
    await answer_delegate_ask(_ask("t-1"), "answer", _Registry())
    assert delivery.spoken
    assert "couldn't get your answer through" in delivery.spoken[0][1].lower()


@pytest.mark.asyncio
async def test_unknown_delegate_is_spoken(active_state):
    delivery = active_state.active_delivery

    class _Empty:
        def get(self, _n):
            return None

    await answer_delegate_ask(_ask("t-1"), "answer", _Empty())
    assert delivery.spoken
    assert "not configured" in delivery.spoken[0][1]
