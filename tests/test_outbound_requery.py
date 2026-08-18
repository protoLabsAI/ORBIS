"""Tests for #678 Phase B2: A2AClient.get_task, the reconnect requery of
the outbound-task registry, and push-back task correlation."""

from __future__ import annotations

import pytest

import agent.delegate_adapters as adapters
from a2a_outbound import A2AClient, A2ADispatchError
from a2a_server import _extract_task_meta
from agent.delegates import Delegate
from agent.outbound_requery import requery_outbound
from memory import Memory


@pytest.fixture
def mem(tmp_path, monkeypatch):
    m = Memory(tmp_path / "t.sqlite")
    monkeypatch.setattr(adapters, "_MEMORY_PROVIDER", lambda: m)
    yield m
    m.close()


# --- A2AClient.get_task -----------------------------------------------------


def _completed_task(task_id: str, answer: str):
    from a2a.types import Artifact, Part, Task, TaskState

    t = Task()
    t.id = task_id
    t.status.state = TaskState.TASK_STATE_COMPLETED
    art = Artifact()
    art.parts.append(Part(text=answer))
    t.artifacts.append(art)
    return t


@pytest.mark.asyncio
async def test_get_task_maps_terminal_result(monkeypatch):
    client = A2AClient(url="http://hub:7871/a2a", name="hub")

    class _Sdk:
        async def get_task(self, request):
            assert request.id == "t-7"
            return _completed_task("t-7", "all done")

    async def _fake_ensure():
        return _Sdk()

    monkeypatch.setattr(client, "_ensure_client", _fake_ensure)
    res = await client.get_task("t-7")
    assert res.state == "completed"
    assert res.text == "all done"
    assert res.is_terminal


@pytest.mark.asyncio
async def test_get_task_failure_raises_dispatch_error(monkeypatch):
    client = A2AClient(url="http://hub:7871/a2a", name="hub")

    class _Sdk:
        async def get_task(self, _request):
            raise RuntimeError("remote gone")

    async def _fake_ensure():
        return _Sdk()

    monkeypatch.setattr(client, "_ensure_client", _fake_ensure)
    with pytest.raises(A2ADispatchError, match="tasks/get failed"):
        await client.get_task("t-7")


# --- requery_outbound -------------------------------------------------------


class _FakeRegistry:
    def __init__(self, names):
        self._names = set(names)

    def get(self, name):
        if name in self._names:
            return Delegate(name=name, description="d", type="a2a",
                            url=f"http://{name}:1/a2a")
        return None


class _FakeDelivery:
    def __init__(self):
        self.delivered: list[tuple[str, str]] = []

    async def deliver(self, text, *, priority=None, source=""):
        self.delivered.append((source, text))


class _FakeResult:
    def __init__(self, state, text=""):
        self.state = state
        self.text = text
        self.input_required = state == "input-required"

    @property
    def is_terminal(self):
        return self.state in ("completed", "failed", "canceled")


@pytest.mark.asyncio
async def test_requery_resolves_terminal_and_keeps_live(mem, monkeypatch):
    mem.outbound.record(task_id="done", delegate="hub", query="q1")
    mem.outbound.record(task_id="running", delegate="hub", query="q2")
    mem.outbound.record(task_id="broken", delegate="hub", query="q3")

    class _FakeClient:
        async def get_task(self, task_id):
            if task_id == "done":
                return _FakeResult("completed", "the answer")
            if task_id == "running":
                return _FakeResult("working")
            raise A2ADispatchError("hub: tasks/get failed: boom")

    monkeypatch.setattr(
        adapters.get_adapter("a2a"), "client_for", lambda _d: _FakeClient()
    )
    delivery = _FakeDelivery()
    resolved = await requery_outbound(_FakeRegistry({"hub"}), delivery)

    assert resolved == 1
    assert mem.outbound.get("done")["status"] == "completed"
    assert mem.outbound.get("done")["result"] == "the answer"
    assert mem.outbound.get("running")["status"] == "working"
    assert mem.outbound.get("broken")["status"] == "submitted"  # untouched
    assert len(delivery.delivered) == 1
    src, text = delivery.delivered[0]
    assert src == "hub"
    assert "finished while you were away" in text
    assert "the answer" in text


@pytest.mark.asyncio
async def test_requery_surfaces_input_required_and_keeps_it_live(mem, monkeypatch):
    mem.outbound.record(task_id="ask", delegate="hub", query="q")

    class _FakeClient:
        async def get_task(self, _tid):
            return _FakeResult("input-required", "which repo did you mean?")

    monkeypatch.setattr(
        adapters.get_adapter("a2a"), "client_for", lambda _d: _FakeClient()
    )
    delivery = _FakeDelivery()
    resolved = await requery_outbound(_FakeRegistry({"hub"}), delivery)
    assert resolved == 1
    # Stays live so the question re-surfaces until answered.
    assert mem.outbound.get("ask")["status"] == "input-required"
    assert len(mem.outbound.live()) == 1
    assert "needs input" in delivery.delivered[0][1]


@pytest.mark.asyncio
async def test_requery_skips_unknown_delegate(mem, monkeypatch):
    mem.outbound.record(task_id="orphan", delegate="gone", query="q")
    resolved = await requery_outbound(_FakeRegistry(set()), None)
    assert resolved == 0
    assert mem.outbound.get("orphan")["status"] == "submitted"


@pytest.mark.asyncio
async def test_requery_noop_without_provider(monkeypatch):
    monkeypatch.setattr(adapters, "_MEMORY_PROVIDER", None)
    assert await requery_outbound(_FakeRegistry({"hub"}), None) == 0


# --- push-back task correlation ---------------------------------------------


def test_extract_task_meta_jsonrpc_envelope():
    body = {"result": {"id": "t-1", "status": {"state": "completed"},
                       "artifacts": []}}
    assert _extract_task_meta(body) == ("t-1", "completed")


def test_extract_task_meta_proto_state_name():
    body = {"result": {"id": "t-2", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}}
    assert _extract_task_meta(body) == ("t-2", "input-required")


def test_extract_task_meta_flat_task_id():
    assert _extract_task_meta({"taskId": "t-3", "text": "hi"}) == ("t-3", None)


def test_extract_task_meta_absent():
    assert _extract_task_meta({"text": "hi", "from": "ava"}) == (None, None)
