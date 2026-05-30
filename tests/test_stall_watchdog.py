"""Tests for the stall watchdog (orbis-1sb / E2)."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import TTSSpeakFrame

from agent.stall_watchdog import _STALL_LINES, StallWatchdog


class _Cap(StallWatchdog):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.pushed: list = []

    async def push_frame(self, frame, direction=None):
        self.pushed.append(frame)


@pytest.mark.asyncio
async def test_fires_recovery_when_no_response() -> None:
    w = _Cap(stall_secs=0.01, enabled=True)
    w._responding = False
    await w._fire_after_delay()
    assert len(w.pushed) == 1
    assert isinstance(w.pushed[0], TTSSpeakFrame)
    assert w.pushed[0].text in _STALL_LINES


@pytest.mark.asyncio
async def test_no_fire_if_responding() -> None:
    w = _Cap(stall_secs=0.01, enabled=True)
    w._responding = True
    await w._fire_after_delay()
    assert w.pushed == []


@pytest.mark.asyncio
async def test_arm_then_cancel_stops_it() -> None:
    w = _Cap(stall_secs=10, enabled=True)
    w._arm()
    assert w._timer is not None and not w._timer.done()
    w._cancel()
    assert w._timer is None


def test_disabled_never_arms() -> None:
    w = _Cap(stall_secs=5, enabled=False)
    w._arm()
    assert w._timer is None


def test_pick_no_repeat_and_fish_prefix() -> None:
    fish = StallWatchdog(tts_backend="fish")
    picks = [fish._pick() for _ in range(20)]
    assert all(p.startswith("[softly]") for p in picks)
    reps = sum(1 for i in range(1, len(picks)) if picks[i] == picks[i - 1])
    assert reps == 0
    kok = StallWatchdog(tts_backend="kokoro")
    assert not kok._pick().startswith("[")
