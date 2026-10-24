#!/usr/bin/env python3
"""ORBIS presence / dead-air harness — measure the "where'd you go?" gap.

Deterministic (NO LLM, NO pipeline): for each tool/delegate profile, derive the
user-audible presence timeline from the REAL policy (agent/presence.py, driven
by the real latency_for / ASYNC_TOOL_NAMES / filler Settings), then report the
largest dead-air gap against the presence SLA. A slow tool that leaves the user
in silence shows up as a gap over the floor.

This is the counterpart to evals/run.py: that one measures the DECISION (routing
+ grounding); this one measures PRESENCE (does the user keep hearing a sign of
life while a slow tool runs). Manual harness, but deterministic — so it can also
back a pytest regression guard once the policy is wired through app.py.

    python evals/presence.py             # all profiles, default 8s floor
    python evals/presence.py --floor 6   # stricter SLA
    python evals/presence.py -s delegate # filter by id substring

A profile gives a tool name (tier + async derived from real code) or an explicit
tier/is_async (for hypothetical tools), a completion time, and the times the
delegate streamed note_progress. See agent/presence.py for the policy itself.
"""

from __future__ import annotations

import argparse
import sys

from agent.filler import Latency, Settings
from agent.presence import (
    PRESENCE_FLOOR_SECS,
    PresenceEvent,
    max_dead_air,
    plan_plain_turn,
    plan_presence,
)
from agent.tools import ASYNC_TOOL_NAMES, latency_for

# completion_at / visual_at are seconds after the tool call starts. `visual_at`
# is when the delegate streamed note_progress — VISUAL-only (StatusPill), so it
# does NOT count against the audio dead-air SLA; it grounds the spoken line.
PROFILES: list[dict] = [
    {"id": "reminder_fast", "tool": "schedule_reminder", "completion_at": 0.4,
     "visual_at": (), "note": "fast tool — its own result is the ack, no loop"},
    {"id": "delegate_stream_healthy", "tool": "delegate_to", "completion_at": 20.0,
     "visual_at": (5.0, 10.0, 15.0), "note": "delegate streams note_progress (visual) ~every 5s"},
    {"id": "delegate_no_stream", "tool": "delegate_to", "completion_at": 25.0,
     "visual_at": (), "note": "the WHERE'D YOU GO case — slow delegate, never streams"},
    {"id": "delegate_one_early", "tool": "delegate_to", "completion_at": 30.0,
     "visual_at": (3.0,), "note": "one early visual update, otherwise quiet"},
    {"id": "delegate_sparse", "tool": "delegate_to", "completion_at": 40.0,
     "visual_at": (8.0,), "note": "long delegate, a single sparse visual update"},
    {"id": "orchestrate_steps", "tool": "orchestrate", "completion_at": 35.0,
     "visual_at": (6.0, 14.0, 22.0, 30.0), "note": "multi-step (now classified SLOW)"},
    {"id": "sync_slow_long", "tool": "(hypothetical slow sync)", "tier": Latency.SLOW,
     "is_async": False, "completion_at": 30.0, "visual_at": (),
     "note": "slow SYNC tool — loop now continues past 12s"},
    {"id": "medium_runs_long", "tool": "(hypothetical medium)", "tier": Latency.MEDIUM,
     "is_async": False, "completion_at": 15.0, "visual_at": (),
     "note": "MEDIUM tool that overruns — loop kicks in (robust to mis-classification)"},
    {"id": "medium_quick", "tool": "(hypothetical medium)", "tier": Latency.MEDIUM,
     "is_async": False, "completion_at": 2.0, "visual_at": (),
     "note": "genuine fast-ish MEDIUM — finishes before the first line, no spam"},
    {"id": "hub_delegation_long", "tool": "delegate_to", "completion_at": 300.0,
     "visual_at": (30.0, 90.0, 180.0),
     "note": "5-min hub task (#678 Phase B) — 2 check-ins then ONE spoken yield; "
             "post-yield silence is contractual, come-back via durable handle"},
]


# Plain turns (no tool call): user stops speaking -> slow / empty / frozen LLM.
# first_token_at is seconds after the user stops; None = never arrives.
# micro_ack_at is set only for profiles where the ack is enabled (it is off by
# default in native mode without AEC), so most profiles rely on the watchdog.
PLAIN_PROFILES: list[dict] = [
    {"id": "plain_fast", "first_token_at": 1.0, "note": "normal turn — no recovery needed"},
    {"id": "plain_slow_first_token", "first_token_at": 15.0,
     "note": "HTTP 200 but first token at 15s — stall watchdog speaks at 8s"},
    {"id": "plain_slow_with_ack", "first_token_at": 15.0, "micro_ack_at": 2.5,
     "note": "same, with micro-ack enabled (AEC active)"},
    {"id": "plain_empty_turn", "first_token_at": 2.0, "empty": True,
     "note": "LLM ends with no text/tool call — recovery line the moment it ends"},
    {"id": "plain_frozen", "first_token_at": None,
     "note": "endpoint never answers — stall line at 8s (UI escalates to 'no response' at 20s)"},
]


def _derive(p: dict) -> tuple[Latency, bool]:
    """tier + is_async: explicit override (hypothetical tools) else from real code."""
    tier = p.get("tier") or latency_for(p["tool"])
    is_async = p["is_async"] if "is_async" in p else (p["tool"] in ASYNC_TOOL_NAMES)
    return tier, is_async


def _timeline(events: list[PresenceEvent]) -> str:
    # ◇ = visual-only (StatusPill, silent); the rest are audible.
    glyph = {"ack": "▸", "progress": "·", "visual": "◇", "result": "✓", "stall": "!", "recovery": "!"}
    return "  ".join(f"{glyph.get(e.kind, '?')}{e.at:g}s {e.kind}" for e in events)


def main() -> None:
    ap = argparse.ArgumentParser(description="ORBIS presence / dead-air harness")
    ap.add_argument("-s", "--scenario", default="", help="filter profiles by id substring")
    ap.add_argument("--floor", type=float, default=PRESENCE_FLOOR_SECS,
                    help=f"presence SLA: max tolerable dead-air gap (default {PRESENCE_FLOOR_SECS}s)")
    args = ap.parse_args()

    profiles = [p for p in PROFILES if args.scenario in p["id"]]
    if not profiles and not any(args.scenario in p["id"] for p in PLAIN_PROFILES):
        sys.exit(f"no profiles match {args.scenario!r}")

    settings = Settings()
    print(f"presence floor = {args.floor:g}s   "
          f"(spoken loop: first @ {settings.progress_first_secs:g}s, "
          f"then every {settings.progress_interval_secs:g}s | ◇ = visual-only)\n")
    print(f"{'profile':<24} {'tier':<6} {'async':<6} {'done':>6} {'max-gap':>8}  verdict")
    print("-" * 72)

    fails = 0
    for p in profiles:
        tier, is_async = _derive(p)
        events = plan_presence(
            tool_name=p["tool"], tier=tier, is_async=is_async,
            completion_at=p["completion_at"],
            delegate_progress_at=tuple(p.get("visual_at", ())),
            settings=settings,
        )
        gap, a, b = max_dead_air(events)
        ok = gap <= args.floor
        fails += not ok
        verdict = "ok" if ok else "DEAD AIR"
        print(f"{p['id']:<24} {tier.value:<6} {('yes' if is_async else 'no'):<6} "
              f"{p['completion_at']:>5g}s {gap:>7.1f}s  {verdict}")
        print(f"    {_timeline(events)}")
        if not ok:
            print(f"    └ {gap:.1f}s silence from {a:g}s→{b:g}s — {p['note']}")

    plain = [p for p in PLAIN_PROFILES if args.scenario in p["id"]]
    if plain:
        print("\nplain turns (no tool):")
        for p in plain:
            events = plan_plain_turn(
                first_token_at=p["first_token_at"],
                empty=p.get("empty", False),
                micro_ack_at=p.get("micro_ack_at"),
            )
            gap, a, b = max_dead_air(events)
            ok = gap <= args.floor
            fails += not ok
            print(f"{p['id']:<24} {'':<6} {'':<6} {'':>6} {gap:>7.1f}s  "
                  + ("ok" if ok else "DEAD AIR"))
            print(f"    {_timeline(events)}")
            if not ok:
                print(f"    └ {gap:.1f}s silence from {a:g}s→{b:g}s — {p['note']}")
        profiles = profiles + plain

    n = len(profiles)
    print(f"\n{n - fails}/{n} within the {args.floor:g}s presence floor"
          + (f"  ({fails} dead-air)" if fails else ""))


if __name__ == "__main__":
    main()
