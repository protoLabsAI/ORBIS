"""Tests for the soft-neglect behavior.

Covers day-bucket mood target mapping, the greeting-nudge text, the
no-prior-sessions fallback, and mood is actually set when days pass.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.neglect import (
    _greeting_nudge,
    _mood_targets_for_gap,
    apply_soft_neglect,
    days_since_last_session,
)
from memory import Memory


@pytest.fixture
def mem(tmp_path: Path) -> Memory:
    return Memory(tmp_path / "orbis.sqlite")


def _seed_session(mem: Memory, days_ago: float) -> None:
    ended = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    mem.sessions.add(
        session_id=f"s-{days_ago}",
        started_at=ended,
        ended_at=ended,
        messages=[],
    )


# --- day→bucket mapping -----------------------------------------------------


def test_mood_targets_fresh_gives_no_targets():
    assert _mood_targets_for_gap(0.1) == {}
    assert _mood_targets_for_gap(0.9) == {}


def test_mood_targets_1_2_days_is_welcome_back():
    t = _mood_targets_for_gap(1.5)
    assert t.get("valence", 0) > 0  # welcome-back bump


def test_mood_targets_2_3_days_has_mild_dip():
    t = _mood_targets_for_gap(2.5)
    assert t["valence"] < 0
    assert t["guardedness"] > 0
    # Still mild — clearly less than the multi-day case.
    assert t["guardedness"] < 0.3


def test_mood_targets_multi_day_is_guarded():
    t = _mood_targets_for_gap(5.0)
    assert t["valence"] < -0.2
    assert t["guardedness"] > 0.3


def test_mood_targets_long_absence_clamps():
    """Over a week caps at clearly-reserved but never full-cold (0.7)."""
    t = _mood_targets_for_gap(30.0)
    assert t["guardedness"] <= 0.7
    assert t["guardedness"] >= 0.5


# --- greeting nudge text ----------------------------------------------------


def test_greeting_nudge_under_a_day_is_empty():
    assert _greeting_nudge(0.5) == ""
    assert _greeting_nudge(1.5) == ""


def test_greeting_nudge_days_mentions_gap():
    assert "couple of days" in _greeting_nudge(3.0).lower() or \
           "few days" in _greeting_nudge(3.0).lower() or \
           "acknowledge" in _greeting_nudge(3.0).lower()


def test_greeting_nudge_week_plus_calls_out_absence():
    msg = _greeting_nudge(10.0)
    assert "days" in msg
    assert "absence" in msg.lower() or "awareness" in msg.lower()


# --- integration: days_since_last_session + apply_soft_neglect --------------


def test_no_prior_sessions_returns_none(mem: Memory):
    assert days_since_last_session(mem) is None
    days, nudge = apply_soft_neglect(mem)
    assert days is None
    assert nudge == ""


def test_fresh_prior_session_no_nudge(mem: Memory):
    _seed_session(mem, days_ago=0.05)  # an hour ago
    days, nudge = apply_soft_neglect(mem)
    assert days is not None
    assert days < 1.0
    assert nudge == ""


def test_multi_day_gap_sets_guardedness(mem: Memory):
    """5-day gap from a neutral baseline lands the mood firmly in
    guarded-with-low-valence territory. With step=0.7 from neutral
    (0.0 → target -0.25 valence, +0.35 guardedness) the result is
    -0.175 / +0.245 — visible from turn one."""
    _seed_session(mem, days_ago=5.0)
    days, nudge = apply_soft_neglect(mem)
    assert days is not None
    assert 4.5 < days < 5.5
    assert nudge  # non-empty greeting nudge
    mood = mem.personality.get_mood()
    # 70% of the way from 0 to target (0.35) = 0.245
    assert mood.guardedness > 0.2
    assert mood.valence < 0.0


def test_soft_neglect_preserves_prior_drift(mem: Memory):
    """R15 regression: a previous session's per-turn audio-tags drift
    must not get blown away by the next session's neglect. Drift +
    neglect compose (drift toward target keeps 30% of prior mood)."""
    # Seed with a positive valence — simulating accumulated per-turn
    # drift from prior sessions where the user had warm audio.
    mem.personality.set_mood(valence=0.6, arousal=0.0, guardedness=0.0)
    _seed_session(mem, days_ago=5.0)
    apply_soft_neglect(mem)

    mood = mem.personality.get_mood()
    # Pre-fix (set_mood directly) would land at exactly target=-0.25.
    # Post-fix (drift_mood_toward step=0.7): 0.6 + (-0.25 - 0.6) * 0.7
    # = 0.6 - 0.595 = 0.005. Slightly positive — the warm prior
    # cushioned the neglect dip while still moving substantially.
    assert mood.valence > -0.1, "prior positive drift should partially survive"
    # But the shift is still in the right direction — clearly less than
    # the prior +0.6.
    assert mood.valence < 0.6, "neglect must still pull mood downward"


def test_soft_neglect_is_visible_from_neutral_baseline(mem: Memory):
    """Neglect must still produce a clearly-shifted mood when the prior
    mood is neutral — preserves the original soft-neglect requirement
    from DECISIONS.md ('the shift needs to be visible from turn one')."""
    # Neutral baseline (no prior drift)
    _seed_session(mem, days_ago=7.5)
    apply_soft_neglect(mem)

    mood = mem.personality.get_mood()
    # 7.5 day target = valence -0.35, guardedness 0.55. With step=0.7
    # from 0: valence ≈ -0.245, guardedness ≈ 0.385. Still clearly
    # guarded.
    assert mood.guardedness > 0.3
    assert mood.valence < -0.2


def test_soft_neglect_is_idempotent_within_bucket(mem: Memory):
    """Replaying neglect in the same session bucket converges — the
    drift_mood_toward operation is contractive toward the target."""
    _seed_session(mem, days_ago=3.0)
    apply_soft_neglect(mem)
    first = mem.personality.get_mood()
    # Second call drifts further toward the target. With step=0.7 the
    # second pass takes another 70% of the remaining distance — same
    # day-bucket but the mood approaches the target asymptotically.
    apply_soft_neglect(mem)
    second = mem.personality.get_mood()
    # Each axis must move toward the target, not away — and stay in
    # the same sign as the first pass (not oscillate).
    assert second.valence <= first.valence  # still negative-ward
    assert second.guardedness >= first.guardedness  # still guarded-ward
