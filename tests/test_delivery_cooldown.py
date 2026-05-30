"""Tests for the DeliveryController cooldown/dedup chokepoint (orbis-1gw B1).

deliver() drops a delivery that repeats within the cooldown window —
keyed by an explicit cooldown_key or, by default, the phrase text — so a
stimulus that fires twice can't make the orb double-speak.
"""

from __future__ import annotations

import pytest

import agent.delivery as d
from agent.delivery import DeliveryController, Priority


class _Cap:
    def __init__(self):
        self.emitted: list[str] = []

    async def __call__(self, frame):
        self.emitted.append(getattr(frame, "text", None))


@pytest.fixture
def clock(monkeypatch):
    """Controllable monotonic clock + a known dedup window."""
    t = {"v": 1000.0}
    monkeypatch.setattr(d.time, "monotonic", lambda: t["v"])
    monkeypatch.setattr(d, "_DEDUP_SECS", 12.0)
    return t


def _ctrl():
    c = DeliveryController()
    cap = _Cap()
    c.set_emitter(cap)
    return c, cap


@pytest.mark.asyncio
async def test_identical_phrase_deduped_within_window(clock) -> None:
    c, cap = _ctrl()
    await c.deliver("build is green", priority=Priority.CRITICAL)  # NOW → emits
    await c.deliver("build is green", priority=Priority.CRITICAL)  # dup → dropped
    assert cap.emitted == ["build is green"]


@pytest.mark.asyncio
async def test_redelivers_after_window(clock) -> None:
    c, cap = _ctrl()
    await c.deliver("ping", priority=Priority.CRITICAL)
    clock["v"] += 13.0  # past the 12s window
    await c.deliver("ping", priority=Priority.CRITICAL)
    assert cap.emitted == ["ping", "ping"]


@pytest.mark.asyncio
async def test_different_phrases_not_deduped(clock) -> None:
    c, cap = _ctrl()
    await c.deliver("alpha", priority=Priority.CRITICAL)
    await c.deliver("beta", priority=Priority.CRITICAL)
    assert cap.emitted == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_explicit_cooldown_key_groups_distinct_phrases(clock) -> None:
    c, cap = _ctrl()
    await c.deliver("CPU at 90%", priority=Priority.CRITICAL, cooldown_key="alert:cpu")
    # Different text, same key, within window → suppressed.
    await c.deliver("CPU at 95%", priority=Priority.CRITICAL, cooldown_key="alert:cpu")
    assert cap.emitted == ["CPU at 90%"]


@pytest.mark.asyncio
async def test_cooldown_secs_zero_bypasses(clock) -> None:
    c, cap = _ctrl()
    await c.deliver("dup", priority=Priority.CRITICAL, cooldown_secs=0)
    await c.deliver("dup", priority=Priority.CRITICAL, cooldown_secs=0)
    assert cap.emitted == ["dup", "dup"]


@pytest.mark.asyncio
async def test_explicit_window_overrides_default(clock) -> None:
    c, cap = _ctrl()
    await c.deliver("x", priority=Priority.CRITICAL, cooldown_secs=60)
    clock["v"] += 30.0  # past default 12s but inside the 60s override
    await c.deliver("x", priority=Priority.CRITICAL, cooldown_secs=60)
    assert cap.emitted == ["x"]
