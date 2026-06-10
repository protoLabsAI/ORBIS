"""Presence-policy regression guard (the "where'd you go?" dead-air fix).

Deterministic (no LLM, no pipeline) checks on agent/presence.py — the single
spec of when the user hears a sign of life during a tool call. evals/presence.py
is the exploratory harness; this is the CI floor that keeps the fix in place.

The fix: any NON-FAST tool (sync or async) gets a spoken presence loop that
repeats until the tool finishes — closing the gap where a slow async delegate
(whose note_progress is VISUAL-only) was silent from the opening ack to the
answer. See agent/presence.py + the PR that added this.
"""

from __future__ import annotations

from agent.filler import Latency, Settings
from agent.presence import (
    PRESENCE_FLOOR_SECS,
    max_dead_air,
    passes_sla,
    plan_presence,
    should_run_presence_loop,
)
from agent.tools import ASYNC_TOOL_NAMES, latency_for

_S = Settings()


def _plan(tool, tier, *, is_async=False, completion, visual=()):
    return plan_presence(
        tool_name=tool, tier=tier, is_async=is_async,
        completion_at=completion, delegate_progress_at=visual, settings=_S,
    )


# ── policy predicate ────────────────────────────────────────────────────────

def test_only_fast_tools_skip_the_loop():
    assert not should_run_presence_loop(Latency.FAST)
    assert should_run_presence_loop(Latency.MEDIUM)
    assert should_run_presence_loop(Latency.SLOW)


def test_fast_tool_has_no_ack_or_progress():
    events = _plan("schedule_reminder", Latency.FAST, completion=0.4)
    assert [e.kind for e in events] == ["result"]


# ── the core regression: slow async delegate must not dead-air ──────────────

def test_slow_async_delegate_not_dead_air():
    # delegate_to with NO streamed progress — the exact "where'd you go" case.
    events = _plan("delegate_to", Latency.SLOW, is_async=True, completion=25.0)
    assert passes_sla(events)
    assert max_dead_air(events)[0] <= PRESENCE_FLOOR_SECS
    # and it actually SPEAKS (ack + at least one progress line), not just the ack
    kinds = [e.kind for e in events]
    assert kinds.count("ack") == 1
    assert kinds.count("progress") >= 1


def test_long_call_keeps_talking_no_silent_tail():
    # 40s delegate: the old two-line loop went silent after ~12s. The continuing
    # cadence must keep every gap within the floor right up to the result.
    events = _plan("delegate_to", Latency.SLOW, is_async=True, completion=40.0)
    assert passes_sla(events)
    assert [e.kind for e in events].count("progress") >= 3


def test_medium_overrun_gets_presence():
    # A MEDIUM-classified tool that runs long still must not dead-air — robustness
    # to latency mis-classification (the loop is gated on non-fast, not on SLOW).
    events = _plan("(medium)", Latency.MEDIUM, completion=15.0)
    assert passes_sla(events)


def test_genuine_quick_medium_does_not_spam():
    # A medium tool that finishes before the first progress line speaks nothing
    # but the ack — no over-narration.
    events = _plan("(medium)", Latency.MEDIUM, completion=2.0)
    assert [e.kind for e in events] == ["ack", "result"]


# ── visual (note_progress) is a silent sign of life — excludes from audio SLA ─

def test_visual_updates_do_not_count_as_audio():
    # Construct a plan whose ONLY non-ack/result events are visual, with a wide
    # completion: the audio gap must still be measured across the visual updates
    # (they don't bridge silence for a voice user).
    events = plan_presence(
        tool_name="delegate_to", tier=Latency.FAST,  # fast → no spoken loop
        is_async=True, completion_at=30.0,
        delegate_progress_at=(10.0, 20.0), settings=_S,
    )
    # only visual + result; the audio gap spans 0 → 30 (visuals ignored)
    assert any(e.kind == "visual" for e in events)
    assert max_dead_air(events)[0] == 30.0


# ── latency classification ──────────────────────────────────────────────────

def test_delegate_to_is_slow_and_async():
    assert latency_for("delegate_to") is Latency.SLOW
    assert "delegate_to" in ASYNC_TOOL_NAMES


def test_orchestrate_is_classified_slow():
    # Regression: orchestrate used to default to MEDIUM (understating the slowest
    # tool there is). It is the multi-step loop — it must be SLOW.
    assert latency_for("orchestrate") is Latency.SLOW
    assert "orchestrate" in ASYNC_TOOL_NAMES
