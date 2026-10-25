"""Tests for the stall watchdog (orbis-1sb / E2)."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import TTSSpeakFrame

from agent import user_state
from agent.stall_watchdog import _STALL_LINES, StallWatchdog


@pytest.fixture(autouse=True)
def _no_recent_cancel():
    """Isolate the module-level cancel signal: default each test to 'never
    cancelled' so a cancel from one test can't suppress another (the watchdog
    now consults a forward time window, not just its own arm)."""
    saved = user_state._last_turn_cancel
    user_state._last_turn_cancel = float("-inf")
    yield
    user_state._last_turn_cancel = saved


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
async def test_no_fire_after_recent_cancel() -> None:
    # Regression: a dismiss ("cancel" / "stop listening") keeps the watchdog
    # quiet even when it re-armed on a phantom UserStopped AFTER the cancel
    # (bot-audio bleed on the open full-duplex mic, or a sub-threshold
    # utterance). The old arm-time check missed this and spoke "this is taking a
    # moment" into dead air.
    w = _Cap(stall_secs=0.01, enabled=True)
    w._responding = False
    user_state.note_turn_cancel()  # user dismissed just now
    await w._fire_after_delay()     # the fire window opens after the cancel
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


# --- slow first token / empty turn (dead-air audit) -------------------------

from pipecat.frames.frames import (  # noqa: E402
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    UserStoppedSpeakingFrame,
)


@pytest.mark.asyncio
async def test_response_start_frame_does_not_disarm_watchdog() -> None:
    # pipecat pushes LLMFullResponseStartFrame BEFORE the HTTP call, so a slow
    # first token must still reach the stall timer.
    w = _Cap(stall_secs=10, enabled=True)
    await w._observe(UserStoppedSpeakingFrame())
    await w._observe(LLMFullResponseStartFrame())
    assert w._responding is False
    assert w._timer is not None and not w._timer.done()
    w._cancel()


@pytest.mark.asyncio
async def test_real_output_disarms_watchdog() -> None:
    w = _Cap(stall_secs=10, enabled=True)
    await w._observe(UserStoppedSpeakingFrame())
    await w._observe(LLMTextFrame("hi"))
    assert w._responding is True
    assert w._timer is None


@pytest.mark.asyncio
async def test_slow_first_token_fires_recovery() -> None:
    w = _Cap(stall_secs=0.01, enabled=True)
    await w._observe(UserStoppedSpeakingFrame())
    await w._observe(LLMFullResponseStartFrame())
    await w._timer
    assert len(w.pushed) == 1 and w.pushed[0].text in _STALL_LINES


@pytest.mark.asyncio
async def test_late_empty_completion_after_stall_fire_stays_quiet() -> None:
    w = _Cap(stall_secs=0.01, enabled=True)
    await w._observe(UserStoppedSpeakingFrame())
    await w._observe(LLMFullResponseStartFrame())
    await w._timer
    await w._observe(LLMFullResponseEndFrame())
    assert len(w.pushed) == 1


@pytest.mark.asyncio
async def test_empty_turn_speaks_recovery_without_context() -> None:
    w = _Cap(stall_secs=10, enabled=True)
    await w._observe(UserStoppedSpeakingFrame())
    await w._observe(LLMFullResponseStartFrame())
    await w._observe(LLMFullResponseEndFrame())
    assert len(w.pushed) == 1
    assert isinstance(w.pushed[0], TTSSpeakFrame)
    assert w.pushed[0].append_to_context is False
    assert "say that again" in w.pushed[0].text
    assert w._timer is None


@pytest.mark.asyncio
async def test_turn_with_text_or_tool_call_is_not_empty() -> None:
    for out in (LLMTextFrame("hello"), FunctionCallsStartedFrame(function_calls=[])):
        w = _Cap(stall_secs=10, enabled=True)
        await w._observe(UserStoppedSpeakingFrame())
        await w._observe(LLMFullResponseStartFrame())
        await w._observe(out)
        await w._observe(LLMFullResponseEndFrame())
        assert w.pushed == []


@pytest.mark.asyncio
async def test_empty_response_without_user_turn_is_ignored() -> None:
    # e.g. a proactive/out-of-band LLM run — no user turn awaiting a reply.
    w = _Cap(stall_secs=10, enabled=True)
    await w._observe(LLMFullResponseEndFrame())
    assert w.pushed == []


@pytest.mark.asyncio
async def test_empty_turn_quiet_after_dismiss() -> None:
    w = _Cap(stall_secs=10, enabled=True)
    await w._observe(UserStoppedSpeakingFrame())
    user_state.note_turn_cancel()
    await w._observe(LLMFullResponseEndFrame())
    assert w.pushed == []
    w._cancel()
