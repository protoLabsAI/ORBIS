"""Facts DAL — structured episodic/semantic memories.

Each fact is ``(subject, relation, object)`` plus confidence + bi-temporal
validity. The curator (``decay_and_prune``) applies a 90-day half-life
based on ``last_accessed`` and prunes below a threshold.

This is the "poor-man's Graphiti" shape DECISIONS.md landed on:
Graphiti's schema translated to SQL so it runs embedded on SQLite
without a graph DB. Multi-hop traversal is not supported (SQLite
recursive CTEs are slow past depth 2) — the tradeoff against the
operational simplicity of single-file embedded storage.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import NamedTuple

logger = logging.getLogger(__name__)

HALF_LIFE_DAYS = 90.0
PRUNE_THRESHOLD = 0.2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime:
    """Parse ISO-8601 into a tz-aware datetime. UTC if naïve."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class FactRecord(NamedTuple):
    """Read shape — stripped of internal bookkeeping."""
    id: str
    subject: str
    relation: str
    object: str
    confidence: float
    valid_at: str | None
    invalid_at: str | None
    created_at: str
    source_session_id: str | None


class FactsDAL:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- write --------------------------------------------------------------

    def add(
        self,
        subject: str,
        relation: str,
        obj: str,
        *,
        confidence: float = 1.0,
        valid_at: str | None = None,
        invalid_at: str | None = None,
        source_session_id: str | None = None,
    ) -> str:
        """Insert a new fact. Returns the new fact id."""
        fact_id = str(uuid.uuid4())
        now = _now()
        self.conn.execute(
            """
            INSERT INTO facts
                (id, subject, relation, object, confidence,
                 valid_at, invalid_at, created_at, expired_at,
                 source_session_id, last_accessed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                fact_id, subject, relation, obj, confidence,
                valid_at, invalid_at, now,
                source_session_id, now,
            ),
        )
        # Mirror into FTS.
        self.conn.execute(
            """
            INSERT INTO facts_fts(rowid, subject, relation, object)
            VALUES (last_insert_rowid(), ?, ?, ?)
            """,
            (subject, relation, obj),
        )
        self.conn.commit()
        return fact_id

    def invalidate(self, fact_id: str, at: str | None = None) -> None:
        """Mark a fact as no longer true in the world. The row stays —
        invalidated facts are queryable historically."""
        when = at or _now()
        self.conn.execute(
            "UPDATE facts SET invalid_at = ?, expired_at = ? WHERE id = ?",
            (when, when, fact_id),
        )
        self.conn.commit()

    # ---- read ---------------------------------------------------------------

    def get(self, fact_id: str) -> FactRecord | None:
        row = self.conn.execute(
            "SELECT * FROM facts WHERE id = ?", (fact_id,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def list_for(
        self, subject: str,
        *,
        include_invalid: bool = False,
        limit: int = 100,
    ) -> list[FactRecord]:
        """Return all facts about ``subject``, newest first by default."""
        sql = "SELECT * FROM facts WHERE subject = ?"
        params: list = [subject]
        if not include_invalid:
            sql += " AND invalid_at IS NULL"
        sql += " ORDER BY confidence DESC, created_at DESC LIMIT ?"
        params.append(limit)
        cur = self.conn.execute(sql, params)
        return [self._row_to_record(r) for r in cur.fetchall()]

    def search(
        self, query: str,
        *,
        limit: int = 10,
        include_invalid: bool = False,
    ) -> list[FactRecord]:
        """BM25 search across subject/relation/object. Touches
        ``last_accessed`` on matches so the curator's decay sees
        recency signal."""
        if not query or not query.strip():
            return []
        try:
            sql = """
                SELECT f.*, bm25(facts_fts) AS score
                FROM facts_fts
                JOIN facts f ON f.rowid = facts_fts.rowid
                WHERE facts_fts MATCH ?
            """
            if not include_invalid:
                sql += " AND f.invalid_at IS NULL"
            sql += " ORDER BY score LIMIT ?"
            cur = self.conn.execute(sql, (query.strip(), limit))
            rows = cur.fetchall()
        except sqlite3.OperationalError as exc:
            logger.debug(f"[facts] FTS search error: {exc}")
            return []

        now = _now()
        ids = [r["id"] for r in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            self.conn.execute(
                f"UPDATE facts SET last_accessed = ? WHERE id IN ({placeholders})",
                (now, *ids),
            )
            self.conn.commit()

        return [self._row_to_record(r) for r in rows]

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()
        return int(row["n"]) if row else 0

    # ---- curator -----------------------------------------------------------

    def decay_and_prune(
        self,
        *,
        half_life_days: float = HALF_LIFE_DAYS,
        prune_threshold: float = PRUNE_THRESHOLD,
        now: str | None = None,
    ) -> dict:
        """Apply 90-day half-life to confidence based on ``last_accessed``,
        then prune rows whose confidence fell below the threshold.

        Returns a summary dict::

            {"decayed": <count>, "pruned": <count>}

        Run from a periodic / scheduled task (e.g. weekly cron or a
        lifespan-startup task).
        """
        now_dt = _parse_iso(now) if now else datetime.now(timezone.utc)

        cur = self.conn.execute(
            "SELECT id, confidence, last_accessed FROM facts"
        )
        rows = cur.fetchall()

        decayed = 0
        pruned_ids: list[str] = []

        for row in rows:
            last = row["last_accessed"]
            if not last:
                continue
            try:
                last_dt = _parse_iso(last)
            except ValueError:
                continue
            days_idle = (now_dt - last_dt).total_seconds() / 86_400
            if days_idle <= 0:
                continue
            decay_factor = 0.5 ** (days_idle / half_life_days)
            new_conf = row["confidence"] * decay_factor
            if new_conf < prune_threshold:
                pruned_ids.append(row["id"])
            else:
                self.conn.execute(
                    "UPDATE facts SET confidence = ? WHERE id = ?",
                    (new_conf, row["id"]),
                )
                decayed += 1

        if pruned_ids:
            placeholders = ",".join("?" for _ in pruned_ids)
            self.conn.execute(
                f"DELETE FROM facts WHERE id IN ({placeholders})",
                tuple(pruned_ids),
            )
            # Rebuild FTS after deletes.
            self.conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")

        self.conn.commit()
        return {"decayed": decayed, "pruned": len(pruned_ids)}

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> FactRecord:
        return FactRecord(
            id=row["id"],
            subject=row["subject"],
            relation=row["relation"],
            object=row["object"],
            confidence=float(row["confidence"]),
            valid_at=row["valid_at"],
            invalid_at=row["invalid_at"],
            created_at=row["created_at"],
            source_session_id=row["source_session_id"],
        )
