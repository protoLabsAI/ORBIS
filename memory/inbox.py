"""Inbox DAL — messages pushed in by external systems for the agent
to pull on demand.

Use cases
---------
- Cron job: nightly summary of yesterday's PRs landed in the agent's inbox
- Webhook: a Slack-style integration POSTs a message when something happens
- Other agents: a sister agent can deliver context updates between sessions

Priority model (borrowed from cc-2.18's queued-command pattern)
---------------------------------------------------------------
- ``now``   — surface at the next safe boundary (session start, or
              explicit ``check_inbox(priority_floor='now')``). The runtime
              auto-injects ``now`` items into the session-start system
              prompt so the agent reads them without a tool call.
- ``next``  — default. Surfaces when the agent voluntarily calls
              ``check_inbox`` (which it'll do when the user asks "anything
              new?", or when conversation flow suggests a check).
- ``later`` — only shown when ``check_inbox(priority_floor='later')``
              is called explicitly. Background chatter that shouldn't
              clutter ordinary checks.

The ingestor picks the priority; the agent (or the session-start hook)
decides what to surface. This mirrors cc-2.18's "the runtime almost
never decides — the agent decides" posture: we never force-interrupt
the active turn.

Storage shape (see memory/db.py for the SQL):

    inbox(
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at   TEXT NOT NULL,
      sender       TEXT NOT NULL,
      channel      TEXT,
      subject      TEXT NOT NULL,
      body         TEXT NOT NULL,
      priority     TEXT NOT NULL DEFAULT 'next' CHECK (...),
      delivered_at TEXT
    )
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Literal

Priority = Literal["now", "next", "later"]
_VALID_PRIORITIES: set[str] = {"now", "next", "later"}

# Priority floor → which priorities the floor includes. ``now`` floor
# returns only 'now'; ``next`` returns 'now' + 'next'; ``later`` returns
# everything. Higher-urgency items always surface when a lower floor is
# requested — same shape as cc-2.18's PRIORITY_ORDER.
_FLOOR_INCLUDES: dict[str, tuple[str, ...]] = {
    "now":   ("now",),
    "next":  ("now", "next"),
    "later": ("now", "next", "later"),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InboxDAL:
    """Inbox table accessor. Held by ``Memory`` and surfaced as
    ``mem.inbox``."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- write ------------------------------------------------------------

    def add(
        self,
        *,
        sender: str,
        subject: str,
        body: str,
        channel: str | None = None,
        priority: Priority = "next",
        created_at: str | None = None,
    ) -> int:
        """Insert a new inbox message. Returns the assigned row id.

        ``created_at`` is auto-stamped to UTC now if not supplied; passing
        an explicit timestamp lets webhook ingestors backdate to the
        original event time.

        ``priority`` defaults to 'next' (visible on next agent check).
        Raises ValueError on an unknown priority — better than silently
        falling back so the deployer notices a typo in their ingest call.
        """
        if priority not in _VALID_PRIORITIES:
            raise ValueError(
                f"unknown priority {priority!r}; expected one of {sorted(_VALID_PRIORITIES)}"
            )
        cur = self.conn.execute(
            """
            INSERT INTO inbox (created_at, sender, channel, subject, body, priority)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (created_at or _now_iso(), sender, channel, subject, body, priority),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    # ---- read -------------------------------------------------------------

    def list_unread(
        self,
        *,
        priority_floor: Priority = "next",
        limit: int = 50,
    ) -> list[dict]:
        """Undelivered messages at the given priority floor, newest-first.

        Default floor is 'next' so a vanilla ``check_inbox`` returns
        urgent + normal items but skips background ('later') chatter.
        Use 'later' to drain everything; 'now' to peek only urgent.
        """
        if priority_floor not in _VALID_PRIORITIES:
            raise ValueError(f"unknown priority floor {priority_floor!r}")
        priorities = _FLOOR_INCLUDES[priority_floor]
        placeholders = ",".join("?" * len(priorities))
        cur = self.conn.execute(
            f"""
            SELECT id, created_at, sender, channel, subject, body, priority, delivered_at
            FROM inbox
            WHERE delivered_at IS NULL
              AND priority IN ({placeholders})
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (*priorities, limit),
        )
        return [dict(row) for row in cur.fetchall()]

    def list_all(self, limit: int = 50) -> list[dict]:
        """Return all messages (delivered + undelivered), newest-first."""
        cur = self.conn.execute(
            """
            SELECT id, created_at, sender, channel, subject, body, priority, delivered_at
            FROM inbox
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]

    def count_unread(self, *, priority_floor: Priority = "later") -> int:
        """Total undelivered at the given floor. Default is 'later'
        (count everything) since this is for diagnostics / UI badges."""
        if priority_floor not in _VALID_PRIORITIES:
            raise ValueError(f"unknown priority floor {priority_floor!r}")
        priorities = _FLOOR_INCLUDES[priority_floor]
        placeholders = ",".join("?" * len(priorities))
        cur = self.conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM inbox
            WHERE delivered_at IS NULL AND priority IN ({placeholders})
            """,
            priorities,
        )
        return int(cur.fetchone()["n"])

    # ---- mark / clear -----------------------------------------------------

    def mark_delivered(self, ids: list[int]) -> int:
        """Stamp delivered_at on the supplied ids (no-op for already-
        delivered ones). Returns the number of rows actually updated.

        Empty list is a no-op (no SQL fired). Idempotent — rows that
        are already delivered keep their original delivered_at."""
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        cur = self.conn.execute(
            f"""
            UPDATE inbox
            SET delivered_at = ?
            WHERE id IN ({placeholders}) AND delivered_at IS NULL
            """,
            (_now_iso(), *ids),
        )
        self.conn.commit()
        return cur.rowcount or 0

    def clear(self, *, only_delivered: bool = True) -> int:
        """Hard-delete messages. Defaults to delivered-only (the safe
        garbage-collect mode); ``only_delivered=False`` wipes everything
        and is reserved for tests + a future "reset" admin tool."""
        if only_delivered:
            cur = self.conn.execute(
                "DELETE FROM inbox WHERE delivered_at IS NOT NULL"
            )
        else:
            cur = self.conn.execute("DELETE FROM inbox")
        self.conn.commit()
        return cur.rowcount or 0
