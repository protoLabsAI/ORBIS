"""Tests for the durable outbound-task registry (#678 Phase B):
memory/outbound.py DAL, the A2AClient on_task first-sighting hook, and
the delegate_adapters memory-provider seam."""

from __future__ import annotations

import pytest

import agent.delegate_adapters as adapters
from a2a_outbound import A2AClient
from memory import Memory


@pytest.fixture
def mem(tmp_path):
    m = Memory(tmp_path / "t.sqlite")
    yield m
    m.close()


# --- DAL --------------------------------------------------------------------


def test_record_get_roundtrip(mem):
    mem.outbound.record(task_id="t-1", delegate="hub", query="do the thing",
                        origin_session="s-9", context_id="ctx-1")
    row = mem.outbound.get("t-1")
    assert row["delegate"] == "hub"
    assert row["status"] == "submitted"
    assert row["origin_session"] == "s-9"
    assert row["context_id"] == "ctx-1"


def test_record_upsert_keeps_row_updates_status(mem):
    mem.outbound.record(task_id="t-1", delegate="hub", query="q")
    mem.outbound.record(task_id="t-1", delegate="hub", query="q",
                        status="working")
    assert mem.outbound.get("t-1")["status"] == "working"
    assert len(mem.outbound.live()) == 1


def test_update_terminal_with_result(mem):
    mem.outbound.record(task_id="t-1", delegate="hub", query="q")
    assert mem.outbound.update("t-1", status="completed", result="the answer")
    row = mem.outbound.get("t-1")
    assert row["status"] == "completed"
    assert row["result"] == "the answer"
    assert mem.outbound.live() == []


def test_update_unknown_id_returns_false(mem):
    assert not mem.outbound.update("nope", status="completed")


def test_live_lists_only_non_terminal(mem):
    mem.outbound.record(task_id="a", delegate="hub", query="q1")
    mem.outbound.record(task_id="b", delegate="hub", query="q2",
                        status="input-required")
    mem.outbound.record(task_id="c", delegate="hub", query="q3")
    mem.outbound.update("c", status="failed")
    assert {r["task_id"] for r in mem.outbound.live()} == {"a", "b"}


def test_prune_expires_stale_live_rows(mem):
    mem.outbound.record(task_id="old", delegate="hub", query="q")
    # Backdate updated_at past the live TTL.
    mem.conn.execute(
        "UPDATE outbound_tasks SET updated_at = '2020-01-01T00:00:00+00:00' "
        "WHERE task_id = 'old'"
    )
    mem.conn.commit()
    touched = mem.outbound.prune(live_ttl_hours=1)
    assert touched == 1
    assert mem.outbound.get("old")["status"] == "expired"
    assert mem.outbound.live() == []


def test_query_truncated(mem):
    mem.outbound.record(task_id="t", delegate="hub", query="x" * 5000)
    assert len(mem.outbound.get("t")["query"]) == 400


# --- A2AClient on_task hook -------------------------------------------------


class _Resp:
    def __init__(self, task):
        self._task = task

    def WhichOneof(self, _):
        return "task"

    def HasField(self, f):
        return f == "task"

    @property
    def task(self):
        return self._task


@pytest.mark.asyncio
async def test_on_task_fires_once_at_first_sighting(monkeypatch):
    from a2a.types import Task, TaskState

    client = A2AClient(url="http://hub:7871/a2a", name="hub")

    class _Sdk:
        async def send_message(self, _request):
            t1 = Task()
            t1.id = "t-42"
            t1.context_id = "ctx-7"
            t1.status.state = TaskState.TASK_STATE_SUBMITTED
            yield _Resp(t1)
            t2 = Task()
            t2.id = "t-42"
            t2.status.state = TaskState.TASK_STATE_COMPLETED
            yield _Resp(t2)

    async def _fake_ensure():
        return _Sdk()

    monkeypatch.setattr(client, "_ensure_client", _fake_ensure)
    seen: list[tuple[str, str | None]] = []
    res = await client.send("go", timeout=5.0,
                            on_task=lambda tid, ctx: seen.append((tid, ctx)))
    assert seen == [("t-42", "ctx-7")]  # once, not per-event
    assert res.task_id == "t-42"


@pytest.mark.asyncio
async def test_on_task_failure_never_breaks_send(monkeypatch):
    from a2a.types import Task, TaskState

    client = A2AClient(url="http://hub:7871/a2a", name="hub")

    class _Sdk:
        async def send_message(self, _request):
            t = Task()
            t.id = "t-1"
            t.status.state = TaskState.TASK_STATE_COMPLETED
            yield _Resp(t)

    async def _fake_ensure():
        return _Sdk()

    monkeypatch.setattr(client, "_ensure_client", _fake_ensure)

    def _boom(_tid, _ctx):
        raise RuntimeError("recorder down")

    res = await client.send("go", timeout=5.0, on_task=_boom)
    assert res.state == "completed"


# --- provider seam ----------------------------------------------------------


def test_outbound_dal_noop_without_provider(monkeypatch):
    monkeypatch.setattr(adapters, "_MEMORY_PROVIDER", None)
    assert adapters._outbound_dal() is None


def test_outbound_dal_survives_provider_failure(monkeypatch):
    def _boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(adapters, "_MEMORY_PROVIDER", _boom)
    assert adapters._outbound_dal() is None


def test_outbound_dal_returns_dal(monkeypatch, mem):
    monkeypatch.setattr(adapters, "_MEMORY_PROVIDER", lambda: mem)
    assert adapters._outbound_dal() is mem.outbound
