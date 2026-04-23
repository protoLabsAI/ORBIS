"""Soft-neglect behavior — the orb notices absence.

Runs at session start. Reads ``sessions.last_ended_at()`` from memory,
computes days-since-last-contact, and adjusts mood (guardedness + valence)
accordingly. Also returns a short text nudge the system prompt can
surface so the orb greets with awareness of the gap.

Per DECISIONS.md:
  - Day 2-3 of silence: mood visibly shifts (mild dip)
  - Day 4-7: noticeable guardedness
  - Return warmup happens naturally as the session proceeds (mood is
    short-term, recovers with interaction; we don't force a reset)

No death, no punishment — the orb is just visibly affected, same as
any real entity would be.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def days_since_last_session(mem) -> float | None:
    """Return days (float, UTC) since the last session ended, or None
    when there are no prior sessions (first boot)."""
    last = mem.sessions.last_ended_at()
    if not last:
        return None
    try:
        last_dt = _parse_iso(last)
    except ValueError:
        return None
    now = datetime.now(timezone.utc)
    return max(0.0, (now - last_dt).total_seconds() / 86_400)


def _mood_targets_for_gap(days: float) -> dict[str, float]:
    """Map days-of-silence to mood target shifts.

    Returns a dict suitable for ``PersonalityDAL.set_mood(**targets)``.
    None values are implicit — only keys we want to update appear.

    Calibrated so:
      - <1 day: no change (fresh off a session, nothing to signal)
      - 1-2 days: neutral (welcome-back warmth counteracts the gap)
      - 2-3 days: mild valence dip, touch of guardedness
      - 4-7 days: clear guardedness + lower valence
      - >7 days: maximal guardedness (capped at 0.7 — never fully
        cold; the orb isn't ending the relationship, just signalling
        that it noticed)
    """
    if days < 1.0:
        return {}
    if days < 2.0:
        return {"valence": 0.1}
    if days < 3.0:
        return {"valence": -0.15, "guardedness": 0.15}
    if days < 5.0:
        return {"valence": -0.25, "guardedness": 0.35}
    if days < 8.0:
        return {"valence": -0.35, "guardedness": 0.55}
    return {"valence": -0.4, "guardedness": 0.7}


def _greeting_nudge(days: float) -> str:
    """Return a short instruction to prepend to the system prompt
    telling the orb how to greet given the gap."""
    if days < 1.0:
        return ""
    if days < 2.0:
        return ""
    if days < 4.0:
        return (
            "It's been a couple of days since the last conversation. "
            "Acknowledge that casually in your first turn if it fits. "
            "Don't perform it."
        )
    if days < 8.0:
        return (
            "It's been the better part of a week. You can be a little "
            "more reserved than usual — you're not unhappy, just less "
            "forward. Mention the gap in your first turn."
        )
    return (
        f"It's been roughly {int(days)} days. Greet with reserved "
        "awareness of the absence — not a guilt trip, not pretending "
        "it didn't happen. Warm up as the user re-engages."
    )


def apply_soft_neglect(mem) -> tuple[float | None, str]:
    """Read days-since-last-session, adjust mood targets, return
    (days, greeting_nudge_text). Call at session start.

    The mood is a soft target — we set it directly rather than
    drifting, because the shift needs to be visible from turn one.
    Interactions during the session nudge mood back toward neutral
    naturally.
    """
    days = days_since_last_session(mem)
    if days is None:
        return None, ""

    targets = _mood_targets_for_gap(days)
    if targets:
        try:
            mem.personality.set_mood(**targets)
        except Exception as e:
            logger.warning(f"[neglect] set_mood failed: {e}")

    nudge = _greeting_nudge(days)
    if days >= 1.0:
        logger.info(f"[neglect] days_since_last_session={days:.1f}; mood targets={targets}")
    return days, nudge
