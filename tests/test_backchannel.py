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


# --- resolve_backchannel_enabled: Fish cap + precedence (2026-07-16) -----------
# Backchannels only sound right on the Fish backend, so the resolver forces
# them off on any other backend regardless of how they were requested, and
# otherwise honours behavior > config-toggle > env > default (on, AEC-gated).
from agent.backchannel import resolve_backchannel_enabled  # noqa: E402


def _resolve(backend="fish", behavior=None, toggle=None, env=None):
    return resolve_backchannel_enabled(
        tts_backend=backend,
        behavior_enabled=behavior,
        config_toggle=toggle,
        env_flag=env,
    )


def test_resolve_non_fish_always_off():
    # Every request path is overridden to off on a non-Fish backend.
    for kwargs in (
        {},
        {"behavior": True},
        {"toggle": True},
        {"env": "on"},
    ):
        assert _resolve(backend="kokoro", **kwargs) == (False, False)
    assert _resolve(backend="openai", toggle=True) == (False, False)


def test_resolve_fish_default_on_and_gated():
    # Nothing set on Fish → default on, AEC-gated (stays quiet without AEC).
    assert _resolve() == (True, True)


def test_resolve_fish_behavior_wins_and_bypasses_gate():
    assert _resolve(behavior=True, toggle=False, env="off") == (True, False)
    assert _resolve(behavior=False, toggle=True, env="on") == (False, False)


def test_resolve_fish_config_toggle_over_env():
    assert _resolve(toggle=True, env="off") == (True, False)
    assert _resolve(toggle=False, env="on") == (False, False)


def test_resolve_fish_env_fallback():
    assert _resolve(env="on") == (True, False)
    assert _resolve(env="1") == (True, False)
    assert _resolve(env="off") == (False, False)
    assert _resolve(env="anything-else") == (False, False)
