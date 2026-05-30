"""Tests for proactive-delivery recollection (orbis-3ta).

Real proactive deliveries are recorded into the shared LLMContext as
assistant turns so the orb remembers saying them; fillers / progress
narration are not (that would reopen the append_to_context riffing issue).
"""

from __future__ import annotations

import pytest

from agent.delivery import DeliveryController, Priority


class _Cap:
    def __init__(self):
        self.emitted: list[str] = []

    async def __call__(self, frame):
        self.emitted.append(getattr(frame, "text", None))


class _FakeContext:
    def __init__(self):
        self.messages: list[dict] = []

    def add_message(self, m):
        self.messages.append(m)


def _ctrl(with_context=True):
    c = DeliveryController()
    cap = _Cap()
    c.set_emitter(cap)
    ctx = _FakeContext() if with_context else None
    if ctx is not None:
        c.set_context(ctx)
    return c, cap, ctx


@pytest.mark.asyncio
async def test_proactive_delivery_recorded_as_assistant() -> None:
    c, cap, ctx = _ctrl()
    await c.deliver("your reminder: stretch", priority=Priority.CRITICAL)
    assert cap.emitted == ["your reminder: stretch"]
    assert ctx.messages == [
        {"role": "assistant", "content": "your reminder: stretch"}
    ]


@pytest.mark.asyncio
async def test_next_silence_drain_recorded() -> None:
    c, cap, ctx = _ctrl()
    # TIME_SENSITIVE → NEXT_SILENCE; user not speaking → drains right away.
    await c.deliver("ava finished the task", priority=Priority.TIME_SENSITIVE)
    assert "ava finished the task" in cap.emitted
    assert ctx.messages == [
        {"role": "assistant", "content": "ava finished the task"}
    ]


@pytest.mark.asyncio
async def test_attribution_is_recorded_as_spoken() -> None:
    c, cap, ctx = _ctrl()
    await c.deliver("done", priority=Priority.CRITICAL, source="ava")
    # whatever the orb actually spoke (attributed) is what gets remembered
    assert ctx.messages[0]["content"] == cap.emitted[0]
    assert "ava" in ctx.messages[0]["content"]


@pytest.mark.asyncio
async def test_filler_not_recorded() -> None:
    c, cap, ctx = _ctrl()
    await c.speak_now("um, let me think", source=None)
    assert "um, let me think" in cap.emitted   # spoken
    assert ctx.messages == []                   # but NOT remembered


@pytest.mark.asyncio
async def test_no_context_is_safe() -> None:
    c, cap, _ = _ctrl(with_context=False)
    await c.deliver("hi", priority=Priority.CRITICAL)  # must not raise
    assert cap.emitted == ["hi"]
