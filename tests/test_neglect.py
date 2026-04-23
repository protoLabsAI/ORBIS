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
    _seed_session(mem, days_ago=5.0)
    days, nudge = apply_soft_neglect(mem)
    assert days is not None
    assert 4.5 < days < 5.5
    assert nudge  # non-empty greeting nudge
    mood = mem.personality.get_mood()
    assert mood.guardedness > 0.3
    assert mood.valence < 0.0


def test_soft_neglect_is_idempotent_within_bucket(mem: Memory):
    _seed_session(mem, days_ago=3.0)
    apply_soft_neglect(mem)
    first = mem.personality.get_mood()
    # Calling again within the same day-bucket should produce the same mood.
    apply_soft_neglect(mem)
    second = mem.personality.get_mood()
    assert first.valence == second.valence
    assert first.guardedness == second.guardedness
