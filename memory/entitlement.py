"""Entitlement cache DAL.

Local mirror of Stripe entitlement verification. ORBIS runs offline-
tolerant: the entitlement status (paid customization unlock, etc.) is
verified with Stripe on boot + periodically, and cached locally with
an ``expires_at`` so it survives outages.

The cache is keyed by feature (``customization``, and future shop
entries). ``value`` is whatever payload the verifier wants to store
(e.g. ``"active"`` or a JSON blob with purchase metadata).

The Stripe integration that writes to this table lives in a separate
module; this DAL is only storage.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class EntitlementDAL:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def set(self, key: str, value: str, *, expires_at: str) -> None:
        """Write-through on the cache. ``expires_at`` is ISO-8601 UTC."""
        now = _now()
        self.conn.execute(
            """
            INSERT INTO entitlement_cache (key, value, verified_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value       = excluded.value,
                verified_at = excluded.verified_at,
                expires_at  = excluded.expires_at
            """,
            (key, value, now, expires_at),
        )
        self.conn.commit()

    def get(self, key: str) -> dict | None:
        """Return the cache row (or None). Doesn't check expiry —
        callers use ``is_active`` for that."""
        row = self.conn.execute(
            "SELECT * FROM entitlement_cache WHERE key = ?",
            (key,),
        ).fetchone()
        return dict(row) if row else None

    def is_active(self, key: str, *, now: str | None = None) -> bool:
        """Return True iff a cached row exists and its ``expires_at``
        is in the future."""
        row = self.get(key)
        if not row:
            return False
        try:
            exp = _parse_iso(row["expires_at"])
        except (KeyError, ValueError):
            return False
        now_dt = _parse_iso(now) if now else datetime.now(timezone.utc)
        return exp > now_dt

    def clear(self, key: str) -> None:
        self.conn.execute("DELETE FROM entitlement_cache WHERE key = ?", (key,))
        self.conn.commit()
