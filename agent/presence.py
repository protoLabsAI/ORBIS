"""Presence policy — when the user hears a "sign of life" during a tool call.

A slow tool call is dead air: the LLM is blocked on the result and can't narrate,
so the user is left wondering "where'd you go?". ORBIS fills that window with
(1) an opening ack the instant a non-fast call starts, and (2) for SLOW SYNC
tools, a two-tier "still working" progress loop. ASYNC tools (delegate_to,
orchestrate) are EXPECTED to narrate themselves via DeliveryController.note_progress
as the delegate streams — they get NO time-based loop.

This module is the single, *testable* spec of that schedule. It mirrors the
wiring in ``app.py`` ``on_function_calls_started`` (the opening-ack +
``_progress_loop`` block); app.py should call ``plan_presence`` so the two never
drift. ``evals/presence.py`` measures the resulting timeline against a presence
SLA (max tolerable dead-air gap) — so closing the "where'd you go" gap is a
change to THIS function, measured, not vibes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .filler import Latency, Settings

# The opening ack is queued SYNCHRONOUSLY (canned, instant) the moment a non-fast
# tool starts, so it reaches the user almost immediately — model a small TTS
# lead-in constant rather than 0.
OPENING_ACK_AT = 0.6

# Presence SLA: the longest the user should go without a sign of life during an
# in-flight tool before it reads as dead air. Anchored to the stall-watchdog's
# stall_secs (8 s) — the project's existing "dead air is bad" threshold. The
# watchdog itself can't cover this window (it stands down at tool-start), which
# is exactly why presence is a separate concern.
PRESENCE_FLOOR_SECS = 8.0


@dataclass(frozen=True)
class PresenceEvent:
    at: float  # seconds after the tool call started
    kind: str  # "ack" | "progress" | "delegate_progress" | "result"
    label: str


def plan_presence(
    *,
    tool_name: str,
    tier: Latency,
    is_async: bool,
    completion_at: float,
    delegate_progress_at: tuple[float, ...] = (),
    settings: Settings | None = None,
    ack_at: float = OPENING_ACK_AT,
) -> list[PresenceEvent]:
    """Ordered timeline of user-audible "sign of life" events for one tool call,
    per the CURRENT presence wiring (app.py:on_function_calls_started):

    - non-FAST tool → one opening ack at ``ack_at``
    - SLOW + SYNC tool → two-tier loop at progress_first_secs / progress_second_secs,
      THEN SILENCE (the loop fires exactly twice — see _progress_loop's docstring)
    - ASYNC tool → NO time-based loop; presence comes only from the delegate's
      streamed note_progress (``delegate_progress_at``)
    - the result lands at ``completion_at``

    The fix that gives async delegates a time-based fallback changes only this
    function — and the harness re-measures.
    """
    s = settings or Settings()
    events: list[PresenceEvent] = []

    if tier is not Latency.FAST:
        events.append(PresenceEvent(ack_at, "ack", f"opening ack ({tool_name})"))

    if tier is Latency.SLOW and not is_async:
        for t in (s.progress_first_secs, s.progress_second_secs):
            if 0 < t < completion_at:
                events.append(PresenceEvent(t, "progress", f"still-working @ {t:g}s"))

    if is_async:
        for t in delegate_progress_at:
            if 0 < t < completion_at:
                events.append(
                    PresenceEvent(t, "delegate_progress", f"streamed check-in @ {t:g}s")
                )

    events.append(PresenceEvent(completion_at, "result", "answer delivered"))
    return sorted(events, key=lambda e: e.at)


def max_dead_air(
    events: list[PresenceEvent], *, start: float = 0.0
) -> tuple[float, float, float]:
    """Largest gap between consecutive audible events, from ``start`` through the
    final event. Returns ``(gap, gap_start, gap_end)``."""
    times = [start, *[e.at for e in events]]
    gaps = [(b - a, a, b) for a, b in zip(times, times[1:])]
    return max(gaps, key=lambda g: g[0])


def passes_sla(events: list[PresenceEvent], *, floor: float = PRESENCE_FLOOR_SECS) -> bool:
    """True if no dead-air gap exceeds the presence floor."""
    return max_dead_air(events)[0] <= floor
