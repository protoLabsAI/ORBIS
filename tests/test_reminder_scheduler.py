"""Tests for the reminder scheduler (orbis-2a0) — DAL, scheduler tick, tool."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app as app_module
from agent.delivery import Priority
from agent.scheduler import ReminderScheduler
from memory import Memory


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture
def mem(tmp_path: Path) -> Memory:
    return Memory(tmp_path / "orbis.sqlite")


# --- DAL -------------------------------------------------------------------


def test_add_and_due_roundtrip(mem: Memory) -> None:
    now = datetime.now(timezone.utc)
    past = mem.reminders.add(text="past one", fire_at=_iso(now - timedelta(minutes=1)))
    future = mem.reminders.add(text="future one", fire_at=_iso(now + timedelta(hours=1)))
    due = mem.reminders.due(_iso(now))
    ids = {r["id"] for r in due}
    assert past in ids and future not in ids
    assert mem.reminders.pending() and len(mem.reminders.pending()) == 2


def test_mark_fired_removes_from_due(mem: Memory) -> None:
    now = datetime.now(timezone.utc)
    rid = mem.reminders.add(text="x", fire_at=_iso(now - timedelta(seconds=1)))
    mem.reminders.mark_fired(rid)
    assert mem.reminders.due(_iso(now)) == []
    assert mem.reminders.pending() == []


def test_add_requires_text(mem: Memory) -> None:
    with pytest.raises(ValueError):
        mem.reminders.add(text="  ", fire_at=_iso(datetime.now(timezone.utc)))


# --- scheduler tick --------------------------------------------------------


class FakeDelivery:
    def __init__(self):
        self.calls: list[tuple] = []

    async def deliver(self, phrase, *, priority=None, source=None, cooldown_key=None, **kw):
        self.calls.append((phrase, priority, source, cooldown_key))


@pytest.mark.asyncio
async def test_fire_due_delivers_and_marks_fired(mem: Memory) -> None:
    now = datetime.now(timezone.utc)
    mem.reminders.add(text="stretch", fire_at=_iso(now - timedelta(seconds=5)))
    fake = FakeDelivery()
    sched = ReminderScheduler(memory_provider=lambda: mem, delivery_provider=lambda: fake)

    n = await sched.fire_due(now=now)
    assert n == 1
    phrase, priority, _src, key = fake.calls[0]
    assert phrase == "stretch"
    assert priority == Priority.TIME_SENSITIVE
    assert key and key.startswith("reminder:")
    assert mem.reminders.due(_iso(now)) == []  # marked fired


@pytest.mark.asyncio
async def test_stale_reminder_dropped_not_spoken(mem: Memory) -> None:
    now = datetime.now(timezone.utc)
    # 30h overdue — past the 24h staleness window.
    mem.reminders.add(text="ancient", fire_at=_iso(now - timedelta(hours=30)))
    fake = FakeDelivery()
    sched = ReminderScheduler(memory_provider=lambda: mem, delivery_provider=lambda: fake)

    n = await sched.fire_due(now=now)
    assert n == 0
    assert fake.calls == []                  # never spoken
    assert mem.reminders.due(_iso(now)) == []  # but marked fired (dropped)


@pytest.mark.asyncio
async def test_no_live_session_leaves_reminder_for_retry(mem: Memory) -> None:
    now = datetime.now(timezone.utc)
    mem.reminders.add(text="soon", fire_at=_iso(now - timedelta(seconds=5)))
    sched = ReminderScheduler(memory_provider=lambda: mem, delivery_provider=lambda: None)

    n = await sched.fire_due(now=now)
    assert n == 0
    # Still due (not marked fired) → fires on a later tick once connected.
    assert len(mem.reminders.due(_iso(now))) == 1


# --- schedule_reminder tool ------------------------------------------------


class FakeParams:
    def __init__(self, arguments):
        self.arguments = arguments
        self.results: list[str] = []

    async def result_callback(self, result: str) -> None:
        self.results.append(result)


@pytest.mark.asyncio
async def test_schedule_reminder_tool_stores(mem: Memory, monkeypatch) -> None:
    from agent.tools import schedule_reminder_handler
    monkeypatch.setattr(app_module, "get_memory", lambda: mem)

    p = FakeParams({"in_minutes": 10, "text": "take the cookies out"})
    await schedule_reminder_handler(p)  # type: ignore[arg-type]
    assert p.results and "remind you" in p.results[0].lower()
    pending = mem.reminders.pending()
    assert len(pending) == 1
    assert pending[0]["text"] == "take the cookies out"
    # fire_at ~10 min out
    fire_at = datetime.fromisoformat(pending[0]["fire_at"])
    delta = (fire_at - datetime.now(timezone.utc)).total_seconds()
    assert 9 * 60 < delta < 11 * 60


@pytest.mark.asyncio
async def test_schedule_reminder_tool_rejects_bad_args(mem: Memory, monkeypatch) -> None:
    from agent.tools import schedule_reminder_handler
    monkeypatch.setattr(app_module, "get_memory", lambda: mem)
    p = FakeParams({"in_minutes": 0, "text": "x"})
    await schedule_reminder_handler(p)  # type: ignore[arg-type]
    assert mem.reminders.pending() == []
    assert "need" in p.results[0].lower()
