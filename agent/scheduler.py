"""Reminder scheduler — the orb's first proactive-initiation surface (orbis-2a0).

A small async loop, started from the app lifespan, that polls the
``reminders`` table and speaks due reminders through the DeliveryController
when a voice session is live. This is the first producer that wakes the orb
*on its own* (no user prompt, no external push) — Phase C of the
proactive-agent roadmap (docs/proactive-agent-direction.md).

Design notes:
- **Idle-safe by construction.** Reminders deliver at ``TIME_SENSITIVE``
  priority, which the DeliveryController drains at the next natural pause —
  it never barges over the user mid-utterance.
- **Chatterbox-safe by construction.** Each delivery carries a
  ``cooldown_key`` so the B1 dedup gate can't double-speak one reminder if a
  tick overlaps.
- **Missed-fire window.** A reminder more than ``max_stale_secs`` overdue
  (default 24h — the Mac was asleep / app closed) is dropped silently rather
  than vocalised late, so waking up never triggers a backlog flood
  (protoAgent's missed-fire pattern).
- **No live session → retry.** If nothing is connected when a reminder comes
  due, it's left unfired and retried next tick, so it lands when the user
  returns (within the staleness window).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from .delivery import DeliveryController, Priority

logger = logging.getLogger(__name__)

# Default poll cadence. A reminder fires within ~one tick of its time; 20s
# keeps it responsive without busy-looping the DB.
_DEFAULT_TICK_SECS = 20.0
# Reminders this far overdue are dropped silently (Mac asleep / app closed).
_DEFAULT_MAX_STALE_SECS = 24 * 3600


class ReminderScheduler:
    def __init__(
        self,
        *,
        memory_provider: Callable[[], object],
        delivery_provider: Callable[[], DeliveryController | None],
        tick_secs: float = _DEFAULT_TICK_SECS,
        max_stale_secs: float = _DEFAULT_MAX_STALE_SECS,
    ) -> None:
        self._memory_provider = memory_provider
        self._delivery_provider = delivery_provider
        self._tick_secs = tick_secs
        self._max_stale_secs = max_stale_secs

    async def fire_due(self, *, now: datetime | None = None) -> int:
        """Fire every reminder that's due. Returns the number spoken.

        Separated from the loop so it's unit-testable with injected
        providers + a fixed clock.
        """
        now = now or datetime.now(timezone.utc)
        mem = self._memory_provider()
        spoken = 0
        for r in mem.reminders.due(now.isoformat()):
            try:
                fire_at = datetime.fromisoformat(r["fire_at"])
            except (ValueError, KeyError):
                # Unparseable row — drop it so it can't wedge the loop.
                mem.reminders.mark_fired(r["id"])
                continue
            repeat = r.get("repeat_secs")

            def _advance() -> None:
                """Retire a one-shot, or roll a recurring one to its next
                occurrence (from now, so a long sleep doesn't queue a burst)."""
                if repeat:
                    nxt = now + timedelta(seconds=repeat)
                    mem.reminders.reschedule(r["id"], nxt.isoformat())
                else:
                    mem.reminders.mark_fired(r["id"])

            overdue = (now - fire_at).total_seconds()
            if overdue > self._max_stale_secs:
                logger.info(
                    f"[scheduler] skipping stale reminder #{r['id']} "
                    f"({overdue / 3600:.1f}h overdue, repeat={bool(repeat)}): "
                    f"{r['text'][:48]!r}"
                )
                _advance()
                continue
            delivery = self._delivery_provider()
            if delivery is None:
                # No live session — leave unfired; retry next tick so it
                # lands when the user reconnects.
                continue
            logger.info(
                f"[scheduler] firing reminder #{r['id']} "
                f"(repeat={bool(repeat)}): {r['text'][:48]!r}"
            )
            await delivery.deliver(
                r["text"],
                priority=Priority.TIME_SENSITIVE,
                source=r.get("source") or None,
                cooldown_key=f"reminder:{r['id']}",
                kind="reminder",
            )
            _advance()
            spoken += 1
        return spoken

    async def run(self) -> None:
        """Poll loop — start once from the app lifespan via create_task."""
        logger.info(
            f"[scheduler] reminder loop started (tick={self._tick_secs}s, "
            f"stale>{self._max_stale_secs / 3600:.0f}h dropped)"
        )
        while True:
            try:
                await self.fire_due()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[scheduler] tick failed: {e}")
            await asyncio.sleep(self._tick_secs)
