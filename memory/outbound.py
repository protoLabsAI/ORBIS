"""Outbound-tasks DAL — durable handles for work delegated to A2A agents.

The audited failure mode this fixes (#678 Phase B): an in-flight
``delegate_to`` was an anonymous coroutine — barge-in silently dropped
the result, disconnect cancelled it, restart lost it, and nothing could
list, requery, or resume the work. Rows here are written by the A2A
adapter the moment the remote agent's first task event arrives (~tens of
ms in), so the handle survives everything that used to destroy the work.

Statuses mirror A2A task states: ``submitted`` / ``working`` /
``input-required`` are live (requeried on reconnect via ``tasks/get``);
``completed`` / ``failed`` / ``canceled`` are terminal. ``expired`` is
ORBIS-local — stamped by ``prune()`` when a live row outlives the TTL.

Storage shape (see memory/db.py for the SQL):

    outbound_tasks(
      task_id        TEXT PRIMARY KEY,
      delegate       TEXT NOT NULL,
      context_id     TEXT,
      origin_session TEXT,
      query          TEXT NOT NULL,
      status         TEXT NOT NULL,
      result         TEXT,
      created_at     TEXT NOT NULL,
      updated_at     TEXT NOT NULL
    )
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

LIVE_STATUSES = ("submitted", "working", "input-required")
TERMINAL_STATUSES = ("completed", "failed", "canceled", "expired")

_QUERY_MAX = 400
_RESULT_MAX = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OutboundTasksDAL:
    """Outbound-tasks accessor. Held by ``Memory`` as ``mem.outbound``."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record(
        self,
        *,
        task_id: str,
        delegate: str,
        query: str,
        origin_session: str | None = None,
        context_id: str | None = None,
        status: str = "submitted",
    ) -> None:
        """Upsert the handle for a dispatched task. Called at first task
        sighting (usually ``submitted``); re-recording with a later state
        just updates status/updated_at and keeps the original row."""
        now = _now_iso()
        self.conn.execute(
            """
            INSERT INTO outbound_tasks
                (task_id, delegate, context_id, origin_session, query,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status     = excluded.status,
                updated_at = excluded.updated_at
            """,
            (task_id, delegate, context_id, origin_session,
             (query or "")[:_QUERY_MAX], status, now, now),
        )
        self.conn.commit()

    def update(
        self,
        task_id: str,
        *,
        status: str,
        result: str | None = None,
    ) -> bool:
        """Move a task to a new status (typically terminal), optionally
        storing an answer preview. Returns False for an unknown id."""
        cur = self.conn.execute(
            """
            UPDATE outbound_tasks
            SET status = ?, updated_at = ?,
                result = COALESCE(?, result)
            WHERE task_id = ?
            """,
            (status, _now_iso(),
             result[:_RESULT_MAX] if result else None, task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get(self, task_id: str) -> sqlite3.Row | None:
        cur = self.conn.execute(
            "SELECT * FROM outbound_tasks WHERE task_id = ?", (task_id,)
        )
        return cur.fetchone()

    def live(self) -> list[sqlite3.Row]:
        """Non-terminal tasks — the reconnect/restart requery set."""
        cur = self.conn.execute(
            f"""
            SELECT * FROM outbound_tasks
            WHERE status IN ({','.join('?' * len(LIVE_STATUSES))})
            ORDER BY created_at
            """,
            LIVE_STATUSES,
        )
        return list(cur.fetchall())

    def prune(self, *, keep_days: int = 7, live_ttl_hours: int = 24) -> int:
        """Housekeeping: delete terminal rows older than ``keep_days``;
        expire live rows not updated within ``live_ttl_hours`` (a remote
        that vanished — requerying forever would spam a dead peer).
        Returns rows touched."""
        now = datetime.now(timezone.utc)
        terminal_cutoff = (now - timedelta(days=keep_days)).isoformat()
        live_cutoff = (now - timedelta(hours=live_ttl_hours)).isoformat()
        cur = self.conn.execute(
            f"""
            DELETE FROM outbound_tasks
            WHERE status IN ({','.join('?' * len(TERMINAL_STATUSES))})
              AND updated_at < ?
            """,
            (*TERMINAL_STATUSES, terminal_cutoff),
        )
        touched = cur.rowcount
        cur = self.conn.execute(
            f"""
            UPDATE outbound_tasks SET status = 'expired', updated_at = ?
            WHERE status IN ({','.join('?' * len(LIVE_STATUSES))})
              AND updated_at < ?
            """,
            (_now_iso(), *LIVE_STATUSES, live_cutoff),
        )
        touched += cur.rowcount
        self.conn.commit()
        return touched
