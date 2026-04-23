"""Smoke tests for the SQLite memory backend.

Covers schema creation, DAL round-trips, FTS search, fact decay, and
entitlement expiry. Each test gets a fresh temp DB so there's no
cross-pollution.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memory import Memory


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def mem(tmp_path: Path) -> Memory:
    return Memory(tmp_path / "orbis.sqlite")


# --- schema -----------------------------------------------------------------


def test_schema_creates_tables(mem: Memory):
    rows = mem.conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table') ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    for expected in (
        "_meta", "sessions", "facts", "personality_axes", "personality_events",
        "mood", "entitlement_cache",
    ):
        assert expected in names, f"missing table: {expected}"


def test_schema_records_version(mem: Memory):
    row = mem.conn.execute(
        "SELECT value FROM _meta WHERE key = 'schema_version'"
    ).fetchone()
    assert row is not None
    assert int(row["value"]) == 1


# --- sessions ---------------------------------------------------------------


def test_sessions_add_and_get(mem: Memory):
    mem.sessions.add(
        session_id="s-1",
        started_at="2026-04-20T10:00:00+00:00",
        ended_at="2026-04-20T10:05:00+00:00",
        messages=[
            {"role": "user", "content": "Hey orb, what's up"},
            {"role": "assistant", "content": "Not much; waiting on you."},
        ],
        final_output="Not much; waiting on you.",
        trace_id="trace-abc",
    )
    row = mem.sessions.get("s-1")
    assert row is not None
    assert row["session_id"] == "s-1"
    assert row["final_output"] == "Not much; waiting on you."
    assert row["trace_id"] == "trace-abc"
    assert len(row["messages"]) == 2
    assert row["messages"][0]["role"] == "user"


def test_sessions_prior_n_is_newest_first(mem: Memory):
    for i in range(3):
        mem.sessions.add(
            session_id=f"s-{i}",
            started_at=f"2026-04-{20 + i:02d}T10:00:00+00:00",
            ended_at=f"2026-04-{20 + i:02d}T10:05:00+00:00",
            messages=[], final_output=f"session {i}",
        )
    recent = mem.sessions.prior_n(2)
    assert len(recent) == 2
    assert recent[0]["session_id"] == "s-2"
    assert recent[1]["session_id"] == "s-1"


def test_sessions_last_ended_at(mem: Memory):
    assert mem.sessions.last_ended_at() is None
    mem.sessions.add(
        session_id="s-1",
        started_at="2026-04-20T10:00:00+00:00",
        ended_at="2026-04-20T10:05:00+00:00",
        messages=[],
    )
    assert mem.sessions.last_ended_at() == "2026-04-20T10:05:00+00:00"


def test_sessions_search_finds_by_content(mem: Memory):
    mem.sessions.add(
        session_id="s-coffee",
        started_at="2026-04-20T10:00:00+00:00",
        ended_at="2026-04-20T10:05:00+00:00",
        messages=[{"role": "user", "content": "I've been drinking too much coffee"}],
        final_output="take a walk",
    )
    mem.sessions.add(
        session_id="s-music",
        started_at="2026-04-21T10:00:00+00:00",
        ended_at="2026-04-21T10:05:00+00:00",
        messages=[{"role": "user", "content": "jazz recs"}],
        final_output="kamasi washington",
    )
    hits = mem.sessions.search("coffee")
    assert any(h["session_id"] == "s-coffee" for h in hits)


# --- facts ------------------------------------------------------------------


def test_facts_add_and_list_for(mem: Memory):
    mem.facts.add("user", "likes", "jazz", confidence=0.9)
    mem.facts.add("user", "lives_in", "Brooklyn", confidence=0.95)
    facts = mem.facts.list_for("user")
    assert len(facts) == 2
    by_relation = {f.relation: f for f in facts}
    assert by_relation["likes"].object == "jazz"
    assert by_relation["lives_in"].object == "Brooklyn"


def test_facts_search_bm25(mem: Memory):
    mem.facts.add("user", "likes", "kamasi washington saxophone jazz")
    mem.facts.add("user", "owns", "a cat named Waffles")
    mem.facts.add("user", "dislikes", "loud rooms")
    hits = mem.facts.search("jazz")
    assert hits
    assert any(f.object.startswith("kamasi") for f in hits)


def test_facts_invalidate_preserves_history(mem: Memory):
    fid = mem.facts.add("user", "lives_in", "Brooklyn")
    mem.facts.invalidate(fid)
    assert mem.facts.list_for("user") == []       # default excludes invalid
    full = mem.facts.list_for("user", include_invalid=True)
    assert len(full) == 1
    assert full[0].invalid_at is not None


def test_facts_decay_and_prune(mem: Memory):
    """Ancient fact (300 days idle at conf=0.9) decays below 0.2 → pruned.
    Fresh fact keeps its confidence. At 90-day half-life:
      300 days idle → decay ≈ 0.5^(300/90) ≈ 0.099 → 0.9 * 0.099 ≈ 0.089 < 0.2.
    """
    mem.facts.add("user", "likes", "old-thing", confidence=0.9)
    mem.facts.add("user", "likes", "new-thing", confidence=0.9)

    # Force the old fact's last_accessed 300 days into the past.
    old_stamp = (datetime.now(timezone.utc) - timedelta(days=300)).isoformat()
    mem.conn.execute(
        "UPDATE facts SET last_accessed = ? WHERE object = 'old-thing'",
        (old_stamp,),
    )
    mem.conn.commit()

    result = mem.facts.decay_and_prune()
    assert result["pruned"] >= 1
    remaining = [f.object for f in mem.facts.list_for("user")]
    assert "new-thing" in remaining
    assert "old-thing" not in remaining


def test_facts_decay_moderate_idle_reduces_confidence_without_pruning(mem: Memory):
    """Moderately idle fact (100 days at conf=0.9) decays but survives.
    At 90-day half-life: 100 days → factor ≈ 0.463 → 0.9 * 0.463 ≈ 0.42.
    """
    mem.facts.add("user", "likes", "kinda-old", confidence=0.9)

    stamp = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    mem.conn.execute(
        "UPDATE facts SET last_accessed = ? WHERE object = 'kinda-old'",
        (stamp,),
    )
    mem.conn.commit()

    result = mem.facts.decay_and_prune()
    assert result["decayed"] >= 1
    assert result["pruned"] == 0
    facts = mem.facts.list_for("user")
    assert len(facts) == 1
    assert 0.35 < facts[0].confidence < 0.5


def test_facts_count(mem: Memory):
    assert mem.facts.count() == 0
    mem.facts.add("user", "likes", "jazz")
    mem.facts.add("user", "likes", "walking")
    assert mem.facts.count() == 2


# --- personality + mood -----------------------------------------------------


def test_personality_seed_defaults(mem: Memory):
    mem.personality.seed_defaults()
    axes = mem.personality.all_axes()
    # The seed should produce ~10 axes, all at 0.0.
    assert len(axes) >= 10
    for a in axes:
        assert a.value == 0.0


def test_personality_drift_accumulates_and_clamps(mem: Memory):
    mem.personality.drift("playful_serious", 0.2, "user made a joke")
    mem.personality.drift("playful_serious", 0.2, "user joked again")
    v = mem.personality.get_axis("playful_serious")
    assert v is not None
    assert abs(v.value - 0.4) < 1e-6
    # Clamping: a big drift gets capped (the warn is on |delta|>0.3).
    mem.personality.drift("playful_serious", 5.0, "huge event")
    v = mem.personality.get_axis("playful_serious")
    assert v.value <= 1.0


def test_personality_drift_logs_events(mem: Memory):
    mem.personality.drift("warm_guarded", -0.1, "user was cold")
    events = mem.personality.recent_events()
    assert events
    e = events[0]
    assert e["axis"] == "warm_guarded"
    assert abs(e["delta"] - (-0.1)) < 1e-6
    assert e["reason"] == "user was cold"


def test_mood_starts_neutral(mem: Memory):
    m = mem.personality.get_mood()
    assert m.valence == 0.0 and m.arousal == 0.0 and m.guardedness == 0.0


def test_mood_partial_update_preserves_other_dims(mem: Memory):
    mem.personality.set_mood(valence=0.5, arousal=-0.3, guardedness=0.1)
    mem.personality.set_mood(valence=0.7)  # update valence only
    m = mem.personality.get_mood()
    assert abs(m.valence - 0.7) < 1e-6
    assert abs(m.arousal - (-0.3)) < 1e-6
    assert abs(m.guardedness - 0.1) < 1e-6


# --- entitlement -------------------------------------------------------------


def test_entitlement_active_when_future_expiry(mem: Memory):
    future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    mem.entitlement.set("customization", "active", expires_at=future)
    assert mem.entitlement.is_active("customization")


def test_entitlement_inactive_when_expired(mem: Memory):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    mem.entitlement.set("customization", "active", expires_at=past)
    assert not mem.entitlement.is_active("customization")


def test_entitlement_inactive_when_missing(mem: Memory):
    assert not mem.entitlement.is_active("nonexistent")


def test_entitlement_clear(mem: Memory):
    future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    mem.entitlement.set("customization", "active", expires_at=future)
    mem.entitlement.clear("customization")
    assert not mem.entitlement.is_active("customization")
