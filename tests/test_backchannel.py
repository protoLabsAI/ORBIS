"""Tests for BackchannelController's AEC gate (L3).

Backchannels default on but must stay silent unless the engine reports
hardware AEC (Apple VPIO). Without it the bot's own speaker bleed crosses the
VAD threshold and a "mm-hmm" fires on her own tail.
"""
from __future__ import annotations

import types

import pytest

from agent.backchannel import BackchannelController
from agent.filler import Verbosity


def _make(aec_gate):
    gen = types.SimpleNamespace(
        settings=types.SimpleNamespace(verbosity=Verbosity.BRIEF)
    )
    return BackchannelController(
        generator=gen, tts_backend="kokoro", enabled=True, aec_gate=aec_gate
    )


def test_no_loop_without_aec():
    """Gate closed → the backchannel loop never starts (safety default)."""
    bc = _make(aec_gate=lambda: False)
    bc._user_speaking = True
    bc._start_loop()
    assert bc._loop_task is None


@pytest.mark.asyncio
async def test_loop_starts_with_aec():
    """Gate open → the loop arms as normal."""
    import asyncio

    bc = _make(aec_gate=lambda: True)
    bc._user_speaking = True
    bc._start_loop()
    assert bc._loop_task is not None
    bc._cancel_loop()
    await asyncio.sleep(0)  # let the cancellation propagate


def test_should_drop_when_aec_lost():
    """An in-flight backchannel is dropped if AEC drops mid-turn."""
    bc = _make(aec_gate=lambda: False)
    bc._user_speaking = True
    bc._bot_speaking = False
    bc._bot_thinking = False
    assert bc._should_drop() is True
