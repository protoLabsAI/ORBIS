"""Personality + mood DALs.

Personality axes are slow-drifting floats in ``[-1, +1]``. Mood is a
short-term state (valence / arousal / guardedness). Both feed into
the system prompt + orb visuals; drift updates are append-only for
auditability.

The initial axis set is defined in ``DEFAULT_AXES`` below. Adding a
new axis is as simple as calling ``upsert_axis(name, value=0.0)`` —
axes that aren't in ``DEFAULT_AXES`` get written through just the
same. The drift logic + prompt composer (elsewhere) iterate whatever
axes exist in the table.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import NamedTuple

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ORBIS ships with ~10 Seaman-flavored axes. Each is neutral (0.0) by
# default and drifts over sessions. ``DEFAULT_AXES`` is only used by
# ``seed_defaults()`` — axes table is schema-free beyond this seed.
DEFAULT_AXES: dict[str, str] = {
    "playful_serious":           "-1 serious, +1 playful",
    "warm_guarded":              "-1 guarded, +1 warm",
    "sarcastic_sincere":         "-1 sincere, +1 sarcastic",
    "verbose_terse":             "-1 terse, +1 verbose",
    "hopeful_cynical":           "-1 cynical, +1 hopeful",
    "grandiose_grounded":        "-1 grounded, +1 grandiose",
    "probing_incurious":         "-1 incurious, +1 probing",
    "philosophical_pragmatic":   "-1 pragmatic, +1 philosophical",
    "independent_clingy":        "-1 clingy, +1 independent",
    "curious_bored":             "-1 bored, +1 curious",
}


class AxisValue(NamedTuple):
    axis: str
    value: float
    updated_at: str


class MoodValue(NamedTuple):
    valence: float
    arousal: float
    guardedness: float
    updated_at: str


class PersonalityDAL:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- axes --------------------------------------------------------------

    def seed_defaults(self) -> None:
        """Insert the DEFAULT_AXES table with neutral values if empty.
        Idempotent — existing rows are untouched."""
        existing = {
            r["axis"] for r in self.conn.execute(
                "SELECT axis FROM personality_axes"
            )
        }
        missing = [a for a in DEFAULT_AXES if a not in existing]
        if not missing:
            return
        now = _now()
        self.conn.executemany(
            "INSERT INTO personality_axes (axis, value, updated_at) VALUES (?, 0.0, ?)",
            [(a, now) for a in missing],
        )
        self.conn.commit()
        logger.info(f"[personality] seeded {len(missing)} default axes")

    def get_axis(self, axis: str) -> AxisValue | None:
        row = self.conn.execute(
            "SELECT * FROM personality_axes WHERE axis = ?",
            (axis,),
        ).fetchone()
        if not row:
            return None
        return AxisValue(
            axis=row["axis"],
            value=float(row["value"]),
            updated_at=row["updated_at"],
        )

    def all_axes(self) -> list[AxisValue]:
        cur = self.conn.execute(
            "SELECT * FROM personality_axes ORDER BY axis"
        )
        return [
            AxisValue(axis=r["axis"], value=float(r["value"]), updated_at=r["updated_at"])
            for r in cur.fetchall()
        ]

    def upsert_axis(self, axis: str, value: float) -> None:
        """Write-through. Value is clamped to [-1, +1]."""
        value = _clamp(value)
        now = _now()
        self.conn.execute(
            """
            INSERT INTO personality_axes (axis, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(axis) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (axis, value, now),
        )
        self.conn.commit()

    def drift(self, axis: str, delta: float, reason: str = "") -> AxisValue:
        """Apply an incremental drift to an axis. Records the event for
        audit + UI visualization. Returns the new AxisValue.

        Deltas are clamped so no single session can flip an axis —
        |delta| > 0.3 is warned + clamped to 0.3. Drift is meant to be
        slow; bigger-than-that shifts should come from explicit
        directable calls via ``upsert_axis``.
        """
        if abs(delta) > 0.3:
            logger.warning(
                f"[personality] drift |delta|={abs(delta):.2f} for {axis!r} clamped to 0.3"
            )
            delta = 0.3 if delta > 0 else -0.3

        current = self.get_axis(axis)
        new_value = _clamp((current.value if current else 0.0) + delta)
        now = _now()
        self.conn.execute(
            """
            INSERT INTO personality_axes (axis, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(axis) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (axis, new_value, now),
        )
        self.conn.execute(
            "INSERT INTO personality_events (axis, delta, reason, at) VALUES (?, ?, ?, ?)",
            (axis, delta, reason, now),
        )
        self.conn.commit()
        return AxisValue(axis=axis, value=new_value, updated_at=now)

    def recent_events(self, limit: int = 50) -> list[dict]:
        """Return recent drift events, newest first."""
        cur = self.conn.execute(
            "SELECT axis, delta, reason, at FROM personality_events ORDER BY at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

    # ---- mood --------------------------------------------------------------

    def get_mood(self) -> MoodValue:
        """Return the current mood row, creating a neutral one on first read."""
        row = self.conn.execute("SELECT * FROM mood WHERE id = 1").fetchone()
        if row is None:
            now = _now()
            self.conn.execute(
                """
                INSERT INTO mood (id, valence, arousal, guardedness, updated_at)
                VALUES (1, 0.0, 0.0, 0.0, ?)
                """,
                (now,),
            )
            self.conn.commit()
            return MoodValue(0.0, 0.0, 0.0, now)
        return MoodValue(
            valence=float(row["valence"]),
            arousal=float(row["arousal"]),
            guardedness=float(row["guardedness"]),
            updated_at=row["updated_at"],
        )

    def set_mood(
        self,
        *,
        valence: float | None = None,
        arousal: float | None = None,
        guardedness: float | None = None,
    ) -> MoodValue:
        """Set any subset of mood dims. Unspecified fields keep their
        current value. All clamped to [-1, +1]."""
        current = self.get_mood()
        new_valence = _clamp(valence if valence is not None else current.valence)
        new_arousal = _clamp(arousal if arousal is not None else current.arousal)
        new_guardedness = _clamp(
            guardedness if guardedness is not None else current.guardedness
        )
        now = _now()
        self.conn.execute(
            """
            INSERT INTO mood (id, valence, arousal, guardedness, updated_at)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                valence     = excluded.valence,
                arousal     = excluded.arousal,
                guardedness = excluded.guardedness,
                updated_at  = excluded.updated_at
            """,
            (new_valence, new_arousal, new_guardedness, now),
        )
        self.conn.commit()
        return MoodValue(new_valence, new_arousal, new_guardedness, now)
