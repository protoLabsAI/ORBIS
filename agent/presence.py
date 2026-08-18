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

# After this many spoken check-ins the loop YIELDS: it speaks one explicit
# "this is taking a while — I'll let you know when it's done" line and goes
# quiet. Post-yield silence is contractual (the user was told), so it doesn't
# count as dead air; the durable outbound-task handle + DeliveryController
# guarantee the come-back. This is the voice adaptation of protoAgent's
# push-only doctrine (#678 Phase B): acknowledge, yield the turn, come back
# once with the answer — a long delegation used to produce an unbounded
# stream of "still working" lines (a 5-minute hub task = ~30 of them).
YIELD_AFTER_CHECKINS = 2

# Canned yield lines — {who} is the delegate target when known, else the tool.
# Spoken ONCE, then the loop stops. Kept canned (not micro-LLM) so the yield
# is instant and can't fail into more dead air.
YIELD_LINES = (
    "{who} is going to take a while on this — I'll let you know the moment it's done.",
    "Still with {who} — this one's slow, so I'll stop narrating and speak up when it lands.",
    "This is taking {who} a bit. I'll quiet down and ping you when it's finished.",
)

# Canned fallback check-ins for when the micro-LLM can't produce a progress
# line (gateway down/slow). Live-QA finding 2026-08-18: a flaky gateway made
# every generated line silently return None, so the loop spoke NOTHING for a
# whole 35s delegation and the (canned!) yield never fired because it was
# gated on spoken lines. The presence SLA is a hard promise — when the
# generator fails, a canned line speaks instead, so a filler outage can
# never reintroduce dead air.
PROGRESS_FALLBACK_LINES = (
    "Still working with {who}.",
    "{who} is still on it.",
    "Hang on — {who} is still going.",
)

# Cap on one progress-line generation. The micro-LLM call must resolve well
# inside the cadence interval; past this we speak the canned fallback. Also
# defends against a HANGING gateway call silently eating the whole loop.
PROGRESS_GEN_TIMEOUT_SECS = 4.0


def progress_fallback_line(who: str, *, pick: int = 0) -> str:
    """A canned check-in line — used when the micro-LLM generator fails or
    times out. ``pick`` selects from the pool (callers pass a random index)."""
    return PROGRESS_FALLBACK_LINES[pick % len(PROGRESS_FALLBACK_LINES)].format(who=who)

# Kinds of event the user actually HEARS — the only ones that count against the
# dead-air SLA. "visual" (a delegate's streamed note_progress on the StatusPill)
# is a real sign of life but a silent one, so it does not.
AUDIO_KINDS = frozenset({"ack", "progress", "yield", "result"})


@dataclass(frozen=True)
class PresenceEvent:
    at: float  # seconds after the tool call started
    kind: str  # "ack" | "progress" | "yield" | "result" (audio) | "visual" (StatusPill only)
    label: str


def yield_line(who: str, *, pick: int = 0) -> str:
    """The spoken yield line. ``pick`` selects from the canned pool (callers
    pass a random index); ``who`` is the delegate target when known."""
    return YIELD_LINES[pick % len(YIELD_LINES)].format(who=who)


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
    yield_after_checkins: int | None = YIELD_AFTER_CHECKINS,
) -> list[PresenceEvent]:
    """Ordered timeline of presence events for one tool call, per the policy
    (which ``app.py:on_function_calls_started`` implements):

    - non-FAST tool → an opening ack at ``ack_at``, then a "still working" line
      at ``progress_first_secs`` and every ``progress_interval_secs`` thereafter
      (FAST tools get neither) — but after ``yield_after_checkins`` spoken
      check-ins the loop speaks ONE yield line and stops: the user has been
      told the work is long and that they'll be pinged, so further narration
      is spam and further silence is excused (``yield_after_checkins=None``
      restores the old unbounded loop)
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
        spoken = 0
        while t < completion_at:
            if yield_after_checkins is not None and spoken >= yield_after_checkins:
                events.append(PresenceEvent(t, "yield", f"yield @ {t:g}s"))
                break
            events.append(PresenceEvent(t, "progress", f"still-working @ {t:g}s"))
            spoken += 1
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
    a voice user looking away hears nothing from them. Silence AFTER a yield
    event is contractual (the user was explicitly told they'll be pinged), so
    the measurement stops at the yield. Returns ``(gap, gap_start, gap_end)``."""
    audible = sorted(
        (e for e in events if e.kind in AUDIO_KINDS), key=lambda e: e.at
    )
    times = [start]
    for e in audible:
        times.append(e.at)
        if e.kind == "yield":
            break
    if len(times) < 2:
        return (0.0, start, start)
    gaps = [(b - a, a, b) for a, b in zip(times, times[1:])]
    return max(gaps, key=lambda g: g[0])


def passes_sla(events: list[PresenceEvent], *, floor: float = PRESENCE_FLOOR_SECS) -> bool:
    """True if no audible dead-air gap exceeds the presence floor."""
    return max_dead_air(events)[0] <= floor
