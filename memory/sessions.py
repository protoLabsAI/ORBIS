"""Sessions DAL — one row per voice session.

Written atomically at session end; read at session start for the
prior-N block injected into the system prompt.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionsDAL:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- write --------------------------------------------------------------

    def add(
        self,
        *,
        session_id: str,
        started_at: str | None,
        ended_at: str | None = None,
        messages: list[dict] | None = None,
        tool_calls: list[dict] | None = None,
        final_output: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Insert a session row. Idempotent on session_id via INSERT OR REPLACE.

        ``messages`` is a list of ``{"role": "...", "content": "..."}``
        dicts. ``tool_calls`` is a list of whatever shape the caller
        wants to preserve (name, args, duration, result).
        """
        if not session_id:
            logger.warning("[sessions] refusing to add row with empty session_id")
            return
        self.conn.execute(
            """
            INSERT OR REPLACE INTO sessions
                (session_id, started_at, ended_at, messages, tool_calls,
                 final_output, trace_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                started_at or _now(),
                ended_at or _now(),
                json.dumps(messages or [], ensure_ascii=False),
                json.dumps(tool_calls or [], ensure_ascii=False),
                final_output,
                trace_id,
            ),
        )
        # Rebuild FTS row for this session (FTS virtual table doesn't
        # auto-mirror INSERT OR REPLACE on the backing table when we
        # use an explicit rowid).
        self.conn.execute(
            "INSERT INTO sessions_fts(sessions_fts) VALUES('rebuild')"
        )
        self.conn.commit()
        logger.info(f"[sessions] persisted session={session_id!r}")

    # ---- read ---------------------------------------------------------------

    def get(self, session_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def prior_n(self, n: int = 10) -> list[dict]:
        """Return the most recent ``n`` sessions, newest first."""
        if n <= 0:
            return []
        cur = self.conn.execute(
            "SELECT * FROM sessions ORDER BY ended_at DESC LIMIT ?",
            (n,),
        )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    def last_ended_at(self) -> str | None:
        """Return ISO-8601 timestamp of the most recent session's end,
        or None if there are no sessions. Used by the soft-neglect
        computation to derive days-since-last-contact."""
        row = self.conn.execute(
            "SELECT ended_at FROM sessions ORDER BY ended_at DESC LIMIT 1"
        ).fetchone()
        return row["ended_at"] if row else None

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Full-text search over session messages + final output. Returns
        rows ordered by BM25 relevance (best first)."""
        if not query or not query.strip():
            return []
        try:
            cur = self.conn.execute(
                """
                SELECT s.*, bm25(sessions_fts) AS score
                FROM sessions_fts
                JOIN sessions s ON s.rowid = sessions_fts.rowid
                WHERE sessions_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (query.strip(), limit),
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except sqlite3.OperationalError as exc:
            logger.debug(f"[sessions] FTS search error: {exc}")
            return []

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        return int(row["n"]) if row else 0

    # ---- helpers ------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Hydrate JSON columns; leave timestamps as ISO strings."""
        d = dict(row)
        for k in ("messages", "tool_calls"):
            if k in d and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except json.JSONDecodeError:
                    d[k] = []
        return d
