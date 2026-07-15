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
        # UPSERT (not INSERT OR REPLACE): on a re-persist of the same
        # session_id this UPDATEs in place, keeping the rowid stable and firing
        # the AFTER UPDATE trigger that syncs sessions_fts incrementally. A
        # REPLACE would delete+reinsert (new rowid) and its delete wouldn't fire
        # the FTS delete trigger without recursive_triggers. The old code
        # sidestepped that with a full `('rebuild')` on every write — O(total
        # content), fired on the event loop at session connect + disconnect,
        # which became a multi-hundred-ms first-turn stall over months (#482).
        self.conn.execute(
            """
            INSERT INTO sessions
                (session_id, started_at, ended_at, messages, tool_calls,
                 final_output, trace_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                started_at   = excluded.started_at,
                ended_at     = excluded.ended_at,
                messages     = excluded.messages,
                tool_calls   = excluded.tool_calls,
                final_output = excluded.final_output,
                trace_id     = excluded.trace_id
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
        self.conn.commit()
        logger.info(f"[sessions] persisted session={session_id!r}")

    def prune(self, *, keep_last: int = 200, max_age_days: int = 90) -> int:
        """Retention sweep: delete sessions older than ``max_age_days`` while
        always keeping the most recent ``keep_last`` regardless of age, so a
        returning user never loses their whole history. Full transcripts are
        JSON blobs that otherwise accumulate forever (#482). Returns the number
        pruned; sessions_fts rows are removed by the AFTER DELETE trigger.
        Called from the curator loop (weekly), not the hot path.
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        cur = self.conn.execute(
            """
            DELETE FROM sessions
            WHERE ended_at < ?
              AND session_id NOT IN (
                  SELECT session_id FROM sessions ORDER BY ended_at DESC LIMIT ?
              )
            """,
            (cutoff, keep_last),
        )
        self.conn.commit()
        pruned = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        if pruned:
            logger.info(
                f"[sessions] pruned {pruned} sessions older than {max_age_days}d "
                f"(kept most recent {keep_last})"
            )
        return pruned

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
