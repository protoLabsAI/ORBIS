"""Reminders DAL — time-based proactive prompts the agent schedules for
itself ("remind me in 10 minutes to take the cookies out").

The scheduler (``agent/scheduler.py``) polls ``due()`` on a timer and
delivers each through the DeliveryController, then calls ``mark_fired()``.
Stored UTC-ISO8601; comparisons are lexicographic on the ISO strings,
which is correct for same-offset (always +00:00) timestamps.

Storage shape (see memory/db.py for the SQL):

    reminders(
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at  TEXT NOT NULL,   -- when scheduled
      fire_at     TEXT NOT NULL,   -- when to fire
      text        TEXT NOT NULL,   -- what to say
      source      TEXT,            -- optional attribution
      fired_at    TEXT             -- set when delivered or dropped
    )
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RemindersDAL:
    """Reminders table accessor. Held by ``Memory`` as ``mem.reminders``."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, *, text: str, fire_at: str, source: str | None = None) -> int:
        """Schedule a reminder. ``fire_at`` is a UTC ISO8601 string. Returns
        the new row id."""
        text = (text or "").strip()
        if not text:
            raise ValueError("reminder text is required")
        if not fire_at:
            raise ValueError("fire_at is required")
        cur = self.conn.execute(
            "INSERT INTO reminders (created_at, fire_at, text, source) "
            "VALUES (?, ?, ?, ?)",
            (_now_iso(), fire_at, text, source),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def due(self, now_iso: str | None = None) -> list[dict]:
        """Unfired reminders whose fire_at has arrived, oldest-first."""
        now_iso = now_iso or _now_iso()
        rows = self.conn.execute(
            "SELECT id, created_at, fire_at, text, source FROM reminders "
            "WHERE fired_at IS NULL AND fire_at <= ? ORDER BY fire_at ASC",
            (now_iso,),
        ).fetchall()
        return [dict(r) for r in rows]

    def pending(self) -> list[dict]:
        """All not-yet-fired reminders, soonest-first."""
        rows = self.conn.execute(
            "SELECT id, created_at, fire_at, text, source FROM reminders "
            "WHERE fired_at IS NULL ORDER BY fire_at ASC",
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_fired(self, reminder_id: int, *, fired_at: str | None = None) -> None:
        self.conn.execute(
            "UPDATE reminders SET fired_at = ? WHERE id = ?",
            (fired_at or _now_iso(), reminder_id),
        )
        self.conn.commit()
