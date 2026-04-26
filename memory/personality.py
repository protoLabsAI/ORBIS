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
        """**Direct-set mood**, overwriting whatever was there.

        Intended for explicit operator overrides (drawer UI, tests) and
        for boot-time seeding. Per-turn writers (audio-tags PR 2) and
        session-open shifts (soft-neglect) should use ``drift_mood_toward``
        so the two systems compose rather than overwriting each other —
        a session-open ``set_mood`` undoes any per-turn drift the prior
        session left behind.

        Unspecified fields keep their current value. All clamped to
        ``[-1, +1]``.
        """
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

    def drift_mood(
        self,
        *,
        valence_delta: float | None = None,
        arousal_delta: float | None = None,
        guardedness_delta: float | None = None,
    ) -> MoodValue:
        """Apply per-turn deltas on top of the current mood — the API
        the per-turn writers (audio-tags / future per-turn affect
        signals) call. Composes with ``drift_mood_toward`` (session-
        open shifts) and ``set_mood`` (explicit overrides) without
        anyone needing to know about the others.

        For each non-None delta:: ``new = current + delta``, clamped to
        ``[-1, +1]``. Unspecified dims keep their current value.

        Doesn't accept a ``step`` — use ``drift_mood_toward`` if you
        need target-blending semantics. The delta API is for "this turn
        added X to the running mood" writes.

        No-op short-circuit: when every delta is None there's nothing
        to write — return the current mood without touching the row.
        Avoids hammering the DB (and bumping ``updated_at``) on every
        per-turn frame that happens to carry no affect signal.
        """
        if (
            valence_delta is None
            and arousal_delta is None
            and guardedness_delta is None
        ):
            return self.get_mood()
        current = self.get_mood()
        new_valence = (
            _clamp(current.valence + valence_delta)
            if valence_delta is not None
            else current.valence
        )
        new_arousal = (
            _clamp(current.arousal + arousal_delta)
            if arousal_delta is not None
            else current.arousal
        )
        new_guardedness = (
            _clamp(current.guardedness + guardedness_delta)
            if guardedness_delta is not None
            else current.guardedness
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

    def drift_mood_toward(
        self,
        *,
        valence: float | None = None,
        arousal: float | None = None,
        guardedness: float | None = None,
        step: float = 0.7,
    ) -> MoodValue:
        """Drift mood a fraction of the way from current toward each
        specified target.

        For each non-None dim, the new value is::

            new = current + (target - current) * step

        ``step=1.0`` is equivalent to ``set_mood`` (snap to target).
        ``step=0.0`` is a no-op. Defaults to ``0.7`` — large enough that
        a session-open shift is still visible from turn one (the
        original soft-neglect requirement) while leaving 30% weight on
        the prior mood, so accumulated per-turn drift from earlier
        sessions isn't blown away.

        This is the API session-open writers (soft-neglect) and per-turn
        writers (future audio-tags) should use. Both blend cleanly into
        a shared mood register — composition, not overwriting.

        Unspecified dims keep their current value. All clamped to
        ``[-1, +1]``.
        """
        if not 0.0 <= step <= 1.0:
            raise ValueError(f"step must be in [0, 1]; got {step}")
        current = self.get_mood()

        def _drift(target: float | None, base: float) -> float:
            if target is None:
                return base
            return _clamp(base + (target - base) * step)

        new_valence = _drift(valence, current.valence)
        new_arousal = _drift(arousal, current.arousal)
        new_guardedness = _drift(guardedness, current.guardedness)
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
