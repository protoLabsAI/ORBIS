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

    def add(
        self,
        *,
        text: str,
        fire_at: str,
        source: str | None = None,
        repeat_secs: int | None = None,
    ) -> int:
        """Schedule a reminder. ``fire_at`` is a UTC ISO8601 string.
        ``repeat_secs`` (optional) makes it recurring — the scheduler
        reschedules the next occurrence instead of marking it fired.
        Returns the new row id."""
        text = (text or "").strip()
        if not text:
            raise ValueError("reminder text is required")
        if not fire_at:
            raise ValueError("fire_at is required")
        if repeat_secs is not None and repeat_secs <= 0:
            raise ValueError("repeat_secs must be positive when set")
        cur = self.conn.execute(
            "INSERT INTO reminders (created_at, fire_at, text, source, repeat_secs) "
            "VALUES (?, ?, ?, ?, ?)",
            (_now_iso(), fire_at, text, source, repeat_secs),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    _COLS = "id, created_at, fire_at, text, source, repeat_secs"

    def due(self, now_iso: str | None = None) -> list[dict]:
        """Unfired reminders whose fire_at has arrived, oldest-first."""
        now_iso = now_iso or _now_iso()
        rows = self.conn.execute(
            f"SELECT {self._COLS} FROM reminders "
            "WHERE fired_at IS NULL AND fire_at <= ? ORDER BY fire_at ASC",
            (now_iso,),
        ).fetchall()
        return [dict(r) for r in rows]

    def pending(self) -> list[dict]:
        """All not-yet-fired reminders, soonest-first."""
        rows = self.conn.execute(
            f"SELECT {self._COLS} FROM reminders "
            "WHERE fired_at IS NULL ORDER BY fire_at ASC",
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_fired(self, reminder_id: int, *, fired_at: str | None = None) -> None:
        """Retire a one-shot reminder (or permanently stop a recurring one)."""
        self.conn.execute(
            "UPDATE reminders SET fired_at = ? WHERE id = ?",
            (fired_at or _now_iso(), reminder_id),
        )
        self.conn.commit()

    def reschedule(self, reminder_id: int, fire_at: str) -> None:
        """Move a recurring reminder's next fire forward — keeps fired_at
        NULL so it stays in the active pool."""
        self.conn.execute(
            "UPDATE reminders SET fire_at = ? WHERE id = ?",
            (fire_at, reminder_id),
        )
        self.conn.commit()

    def cancel(self, reminder_id: int) -> bool:
        """Cancel a pending reminder (one-time or recurring) by id. Sets
        fired_at so it leaves the active pool and a recurring one stops
        repeating. Returns True if a pending row was actually cancelled."""
        cur = self.conn.execute(
            "UPDATE reminders SET fired_at = ? WHERE id = ? AND fired_at IS NULL",
            (_now_iso(), reminder_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def cancel_matching(self, needle: str) -> list[dict]:
        """Cancel all pending reminders whose text contains ``needle``
        (case-insensitive). Returns the cancelled rows. Empty needle cancels
        nothing; use ``cancel_all`` to clear everything."""
        needle = (needle or "").strip().lower()
        if not needle:
            return []
        matches = [r for r in self.pending() if needle in r["text"].lower()]
        for r in matches:
            self.cancel(r["id"])
        return matches

    def cancel_all(self) -> int:
        """Cancel every pending reminder. Returns how many were cancelled."""
        cur = self.conn.execute(
            "UPDATE reminders SET fired_at = ? WHERE fired_at IS NULL",
            (_now_iso(),),
        )
        self.conn.commit()
        return cur.rowcount
