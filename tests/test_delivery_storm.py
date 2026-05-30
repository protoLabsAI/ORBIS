"""Tests for the drop-storm circuit breaker (orbis-1gw B3).

When a producer floods the DeliveryController past _STORM_THRESHOLD within
the window, it speaks ONE notice then suppresses further deliveries until
the rate subsides — so a runaway loop can't make the orb a chatterbox.
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


def _ctrl():
    c = DeliveryController()
    cap = _Cap()
    c.set_emitter(cap)
    return c, cap


@pytest.fixture
def storm_env(monkeypatch):
    t = {"v": 1000.0}
    monkeypatch.setattr(d.time, "monotonic", lambda: t["v"])
    monkeypatch.setattr(d, "_STORM_THRESHOLD", 3)
    monkeypatch.setattr(d, "_STORM_WINDOW_SECS", 60.0)
    monkeypatch.setattr(d, "_DEDUP_SECS", 0.0)  # isolate from B1 dedup
    return t


@pytest.mark.asyncio
async def test_mutes_after_threshold_with_one_notice(storm_env) -> None:
    c, cap = _ctrl()
    for i in range(3):
        await c.deliver(f"msg {i}", priority=Priority.CRITICAL)
    assert cap.emitted == ["msg 0", "msg 1", "msg 2"]  # all pass

    await c.deliver("msg 3", priority=Priority.CRITICAL)  # trips the breaker
    assert cap.emitted[-1] == d._STORM_NOTICE
    assert "msg 3" not in cap.emitted

    await c.deliver("msg 4", priority=Priority.CRITICAL)  # silently suppressed
    assert "msg 4" not in cap.emitted
    assert cap.emitted.count(d._STORM_NOTICE) == 1  # notice fires exactly once


@pytest.mark.asyncio
async def test_recovers_after_window_clears(storm_env) -> None:
    c, cap = _ctrl()
    for i in range(6):
        await c.deliver(f"m{i}", priority=Priority.CRITICAL)  # 3 pass, then muted
    storm_env["v"] += 61.0  # window slides clear
    await c.deliver("back", priority=Priority.CRITICAL)
    assert "back" in cap.emitted


@pytest.mark.asyncio
async def test_disabled_when_threshold_zero(monkeypatch) -> None:
    monkeypatch.setattr(d, "_STORM_THRESHOLD", 0)
    monkeypatch.setattr(d, "_DEDUP_SECS", 0.0)
    c, cap = _ctrl()
    for i in range(20):
        await c.deliver(f"m{i}", priority=Priority.CRITICAL)
    assert len([x for x in cap.emitted if x and x.startswith("m")]) == 20
    assert d._STORM_NOTICE not in cap.emitted
