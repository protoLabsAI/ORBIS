"""Tests for memory.inbox.InboxDAL — the SQLite-backed message queue
external systems push into for the agent to pull.

Covers:
  - add() returns id, persists, defaults created_at
  - list_unread filters out delivered, respects newest-first
  - list_all includes delivered + undelivered
  - mark_delivered is idempotent + only flips undelivered rows
  - clear() defaults to delivered-only
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory import Memory


@pytest.fixture
def mem(tmp_path: Path) -> Memory:
    return Memory(tmp_path / "orbis.sqlite")


def test_add_returns_id_and_persists(mem: Memory):
    msg_id = mem.inbox.add(sender="webhook:slack", subject="Hi", body="hello")
    assert msg_id > 0
    rows = mem.inbox.list_unread()
    assert len(rows) == 1
    assert rows[0]["id"] == msg_id
    assert rows[0]["sender"] == "webhook:slack"
    assert rows[0]["subject"] == "Hi"
    assert rows[0]["body"] == "hello"
    assert rows[0]["delivered_at"] is None


def test_add_auto_stamps_created_at(mem: Memory):
    msg_id = mem.inbox.add(sender="x", subject="s", body="b")
    rows = mem.inbox.list_all()
    assert len(rows) == 1
    assert rows[0]["id"] == msg_id
    # ISO-8601 with timezone offset; just sanity-check shape.
    assert "T" in rows[0]["created_at"]


def test_add_accepts_explicit_created_at(mem: Memory):
    backdated = "2024-01-01T00:00:00+00:00"
    mem.inbox.add(
        sender="cron", subject="s", body="b", created_at=backdated,
    )
    rows = mem.inbox.list_unread()
    assert rows[0]["created_at"] == backdated


def test_list_unread_excludes_delivered(mem: Memory):
    a = mem.inbox.add(sender="x", subject="a", body="A")
    b = mem.inbox.add(sender="x", subject="b", body="B")
    mem.inbox.mark_delivered([a])

    unread = mem.inbox.list_unread()
    assert [r["id"] for r in unread] == [b]

    all_rows = mem.inbox.list_all()
    assert {r["id"] for r in all_rows} == {a, b}


def test_list_unread_newest_first(mem: Memory):
    """Messages ordered by created_at DESC, then id DESC for stability."""
    mem.inbox.add(sender="x", subject="old", body="o", created_at="2024-01-01T00:00:00+00:00")
    mem.inbox.add(sender="x", subject="mid", body="m", created_at="2024-06-01T00:00:00+00:00")
    mem.inbox.add(sender="x", subject="new", body="n", created_at="2025-01-01T00:00:00+00:00")
    rows = mem.inbox.list_unread()
    assert [r["subject"] for r in rows] == ["new", "mid", "old"]


def test_count_unread(mem: Memory):
    assert mem.inbox.count_unread() == 0
    a = mem.inbox.add(sender="x", subject="a", body="A")
    mem.inbox.add(sender="x", subject="b", body="B")
    assert mem.inbox.count_unread() == 2
    mem.inbox.mark_delivered([a])
    assert mem.inbox.count_unread() == 1


def test_mark_delivered_is_idempotent(mem: Memory):
    a = mem.inbox.add(sender="x", subject="a", body="A")
    first = mem.inbox.mark_delivered([a])
    second = mem.inbox.mark_delivered([a])
    assert first == 1
    assert second == 0  # already delivered; no-op


def test_mark_delivered_empty_list_is_noop(mem: Memory):
    """Empty input shouldn't fire SQL. Returns 0 cleanly."""
    assert mem.inbox.mark_delivered([]) == 0


def test_mark_delivered_partial_application(mem: Memory):
    a = mem.inbox.add(sender="x", subject="a", body="A")
    b = mem.inbox.add(sender="x", subject="b", body="B")
    mem.inbox.mark_delivered([a])
    n = mem.inbox.mark_delivered([a, b])
    assert n == 1  # only b flipped this round


def test_clear_defaults_to_delivered_only(mem: Memory):
    a = mem.inbox.add(sender="x", subject="a", body="A")
    mem.inbox.add(sender="x", subject="b", body="B")
    mem.inbox.mark_delivered([a])
    cleared = mem.inbox.clear()
    assert cleared == 1
    assert mem.inbox.count_unread() == 1


def test_clear_all_wipes_everything(mem: Memory):
    a = mem.inbox.add(sender="x", subject="a", body="A")
    mem.inbox.add(sender="x", subject="b", body="B")
    mem.inbox.mark_delivered([a])
    cleared = mem.inbox.clear(only_delivered=False)
    assert cleared == 2
    assert mem.inbox.list_all() == []


def test_channel_field_is_optional(mem: Memory):
    mem.inbox.add(sender="x", subject="a", body="A")
    mem.inbox.add(sender="x", subject="b", body="B", channel="alerts")
    rows = mem.inbox.list_unread()
    channels = {r["channel"] for r in rows}
    assert channels == {None, "alerts"}


# --- Priority ---------------------------------------------------------------


def test_default_priority_is_next(mem: Memory):
    mem.inbox.add(sender="x", subject="a", body="A")
    rows = mem.inbox.list_all()
    assert rows[0]["priority"] == "next"


def test_unknown_priority_rejected(mem: Memory):
    with pytest.raises(ValueError, match="unknown priority"):
        mem.inbox.add(sender="x", subject="a", body="A", priority="urgent")  # type: ignore[arg-type]


def test_priority_floor_now_returns_only_now(mem: Memory):
    mem.inbox.add(sender="x", subject="urgent", body="!", priority="now")
    mem.inbox.add(sender="x", subject="normal", body=".", priority="next")
    mem.inbox.add(sender="x", subject="background", body="-", priority="later")
    rows = mem.inbox.list_unread(priority_floor="now")
    assert [r["subject"] for r in rows] == ["urgent"]


def test_priority_floor_next_returns_now_and_next(mem: Memory):
    """Default floor — covers urgent + normal but skips background."""
    mem.inbox.add(sender="x", subject="urgent", body="!", priority="now")
    mem.inbox.add(sender="x", subject="normal", body=".", priority="next")
    mem.inbox.add(sender="x", subject="background", body="-", priority="later")
    rows = mem.inbox.list_unread()  # default floor = next
    subjects = {r["subject"] for r in rows}
    assert subjects == {"urgent", "normal"}


def test_priority_floor_later_returns_everything(mem: Memory):
    mem.inbox.add(sender="x", subject="urgent", body="!", priority="now")
    mem.inbox.add(sender="x", subject="normal", body=".", priority="next")
    mem.inbox.add(sender="x", subject="background", body="-", priority="later")
    rows = mem.inbox.list_unread(priority_floor="later")
    subjects = {r["subject"] for r in rows}
    assert subjects == {"urgent", "normal", "background"}


def test_count_unread_respects_priority_floor(mem: Memory):
    mem.inbox.add(sender="x", subject="urgent", body="!", priority="now")
    mem.inbox.add(sender="x", subject="normal", body=".", priority="next")
    mem.inbox.add(sender="x", subject="background", body="-", priority="later")
    assert mem.inbox.count_unread() == 3                          # default floor=later
    assert mem.inbox.count_unread(priority_floor="next") == 2     # now + next
    assert mem.inbox.count_unread(priority_floor="now") == 1      # now only
