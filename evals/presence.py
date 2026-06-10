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
from agent.presence import PRESENCE_FLOOR_SECS, PresenceEvent, max_dead_air, plan_presence
from agent.tools import ASYNC_TOOL_NAMES, latency_for

# completion_at / progress_at are seconds after the tool call starts.
PROFILES: list[dict] = [
    {"id": "reminder_fast", "tool": "schedule_reminder", "completion_at": 0.4,
     "progress_at": (), "note": "fast tool — its own result is the ack"},
    {"id": "delegate_stream_healthy", "tool": "delegate_to", "completion_at": 20.0,
     "progress_at": (5.0, 10.0, 15.0), "note": "delegate streams note_progress ~every 5s"},
    {"id": "delegate_no_stream", "tool": "delegate_to", "completion_at": 25.0,
     "progress_at": (), "note": "WHERE'D YOU GO — slow delegate, never streams progress"},
    {"id": "delegate_one_early", "tool": "delegate_to", "completion_at": 30.0,
     "progress_at": (3.0,), "note": "one early check-in, then silence to the answer"},
    {"id": "delegate_sparse", "tool": "delegate_to", "completion_at": 40.0,
     "progress_at": (8.0,), "note": "long delegate, a single sparse update"},
    {"id": "orchestrate_steps", "tool": "orchestrate", "completion_at": 35.0,
     "progress_at": (6.0, 14.0, 22.0, 30.0), "note": "multi-step, per-step reassurance"},
    {"id": "sync_slow_long", "tool": "(hypothetical slow sync)", "tier": Latency.SLOW,
     "is_async": False, "completion_at": 30.0, "progress_at": (),
     "note": "slow SYNC tool — two-line loop (6s,12s) THEN silence"},
    {"id": "medium_runs_long", "tool": "(hypothetical medium)", "tier": Latency.MEDIUM,
     "is_async": False, "completion_at": 15.0, "progress_at": (),
     "note": "medium tool that runs long — opening ack only, no loop"},
]


def _derive(p: dict) -> tuple[Latency, bool]:
    """tier + is_async: explicit override (hypothetical tools) else from real code."""
    tier = p.get("tier") or latency_for(p["tool"])
    is_async = p["is_async"] if "is_async" in p else (p["tool"] in ASYNC_TOOL_NAMES)
    return tier, is_async


def _timeline(events: list[PresenceEvent]) -> str:
    glyph = {"ack": "▸", "progress": "·", "delegate_progress": "·", "result": "✓"}
    return "  ".join(f"{glyph.get(e.kind, '?')}{e.at:g}s {e.kind}" for e in events)


def main() -> None:
    ap = argparse.ArgumentParser(description="ORBIS presence / dead-air harness")
    ap.add_argument("-s", "--scenario", default="", help="filter profiles by id substring")
    ap.add_argument("--floor", type=float, default=PRESENCE_FLOOR_SECS,
                    help=f"presence SLA: max tolerable dead-air gap (default {PRESENCE_FLOOR_SECS}s)")
    args = ap.parse_args()

    profiles = [p for p in PROFILES if args.scenario in p["id"]]
    if not profiles:
        sys.exit(f"no profiles match {args.scenario!r}")

    settings = Settings()
    print(f"presence floor = {args.floor:g}s   "
          f"(progress loop fires at {settings.progress_first_secs:g}s, "
          f"{settings.progress_second_secs:g}s)\n")
    print(f"{'profile':<24} {'tier':<6} {'async':<6} {'done':>6} {'max-gap':>8}  verdict")
    print("-" * 72)

    fails = 0
    for p in profiles:
        tier, is_async = _derive(p)
        events = plan_presence(
            tool_name=p["tool"], tier=tier, is_async=is_async,
            completion_at=p["completion_at"],
            delegate_progress_at=tuple(p.get("progress_at", ())),
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

    n = len(profiles)
    print(f"\n{n - fails}/{n} within the {args.floor:g}s presence floor"
          + (f"  ({fails} dead-air)" if fails else ""))


if __name__ == "__main__":
    main()
