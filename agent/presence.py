"""Presence policy — when the user hears a "sign of life" during a tool call.

A slow tool call is dead air: the LLM is blocked on the result and can't narrate,
so the user is left wondering "where'd you go?". ORBIS fills that window with
SPOKEN presence:

  1. an opening ack the instant a non-fast call starts ("okay, on it"), and
  2. a periodic, sparse "still working" loop that repeats until the tool finishes.

The loop runs for ANY non-fast tool — sync OR async. This is the fix for the
"where'd you go" gap: ``delegate_to`` / ``orchestrate`` are async, and a delegate's
streamed ``note_progress`` is **visual only** (the StatusPill rail — see
DeliveryController.note_progress, which explicitly does NOT speak). So without a
time-based spoken loop, a slow delegate is silent from the opening ack to the
answer no matter how much it streams. The loop also no longer stops after two
lines, so a long call never dead-airs past one interval. Each spoken line grounds
in the delegate's latest streamed status when one is present.

This module is the single, *testable* spec of that schedule. ``app.py``'s
``on_function_calls_started`` calls ``should_run_presence_loop`` and drives the
loop off the same cadence (filler ``Settings``), so the two never drift.
``evals/presence.py`` measures the resulting timeline against a presence SLA
(max tolerable spoken-silence gap); ``tests/test_presence.py`` guards it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .filler import Latency, Settings

# The opening ack is queued SYNCHRONOUSLY (canned, instant) the moment a non-fast
# tool starts, so it reaches the user almost immediately — model a small TTS
# lead-in constant rather than 0.
OPENING_ACK_AT = 0.6

# Presence SLA: the longest the user should go without a SPOKEN sign of life
# while a tool is in flight, before it reads as "where'd you go?" dead air.
# Larger than the stall-watchdog's cold-stall threshold (8 s): the user already
# heard the opening ack, so they know work is happening and tolerate a longer
# beat — and over-narrating reads as spam. The cadence below (first ~6 s, then
# every ~10 s) keeps the worst gap to ~one interval, comfortably under this.
PRESENCE_FLOOR_SECS = 12.0

# Kinds of event the user actually HEARS — the only ones that count against the
# dead-air SLA. "visual" (a delegate's streamed note_progress on the StatusPill)
# is a real sign of life but a silent one, so it does not.
AUDIO_KINDS = frozenset({"ack", "progress", "result"})


@dataclass(frozen=True)
class PresenceEvent:
    at: float  # seconds after the tool call started
    kind: str  # "ack" | "progress" | "result" (audio) | "visual" (StatusPill only)
    label: str


def should_run_presence_loop(tier: Latency) -> bool:
    """Whether a tool gets the time-based spoken presence loop.

    Any NON-FAST tool — i.e. anything that might keep the user waiting. This is
    the fix's core change: the old wiring gated on ``tier is SLOW and not async``,
    which excluded every async delegate (the slow tools that matter most) and
    also any MEDIUM tool that overran. Fast tools return almost instantly, so
    their own result is the acknowledgement; they get neither ack nor loop.
    """
    return tier is not Latency.FAST


def plan_presence(
    *,
    tool_name: str,
    tier: Latency,
    is_async: bool = False,  # retained for call-site clarity; no longer gates the loop
    completion_at: float,
    delegate_progress_at: tuple[float, ...] = (),
    settings: Settings | None = None,
    ack_at: float = OPENING_ACK_AT,
) -> list[PresenceEvent]:
    """Ordered timeline of presence events for one tool call, per the policy
    (which ``app.py:on_function_calls_started`` implements):

    - non-FAST tool → an opening ack at ``ack_at``, then a "still working" line
      at ``progress_first_secs`` and every ``progress_interval_secs`` thereafter
      until ``completion_at`` (FAST tools get neither)
    - ``delegate_progress_at`` are the delegate's streamed note_progress updates —
      rendered as ``"visual"`` events (StatusPill) that do NOT count against the
      audio dead-air SLA; in the live loop they ground the spoken line's wording
    - the result lands at ``completion_at``
    """
    s = settings or Settings()
    events: list[PresenceEvent] = []

    if should_run_presence_loop(tier):
        events.append(PresenceEvent(ack_at, "ack", f"opening ack ({tool_name})"))
        t = s.progress_first_secs
        while t < completion_at:
            events.append(PresenceEvent(t, "progress", f"still-working @ {t:g}s"))
            t += s.progress_interval_secs

    for vt in delegate_progress_at:
        if 0 < vt < completion_at:
            events.append(PresenceEvent(vt, "visual", f"StatusPill update @ {vt:g}s"))

    events.append(PresenceEvent(completion_at, "result", "answer delivered"))
    return sorted(events, key=lambda e: e.at)


def max_dead_air(
    events: list[PresenceEvent], *, start: float = 0.0
) -> tuple[float, float, float]:
    """Largest gap between consecutive AUDIBLE events (``AUDIO_KINDS``), from
    ``start`` through the final audible event. Visual-only updates are ignored —
    a voice user looking away hears nothing from them. Returns
    ``(gap, gap_start, gap_end)``."""
    times = [start, *[e.at for e in events if e.kind in AUDIO_KINDS]]
    gaps = [(b - a, a, b) for a, b in zip(times, times[1:])]
    return max(gaps, key=lambda g: g[0])


def passes_sla(events: list[PresenceEvent], *, floor: float = PRESENCE_FLOOR_SECS) -> bool:
    """True if no audible dead-air gap exceeds the presence floor."""
    return max_dead_air(events)[0] <= floor
