"""Tests for MultiInputMixer."""

from __future__ import annotations

import asyncio
import struct

import pytest

from pipecat.frames.frames import InputAudioRawFrame

from voice.multi_input_mixer import MultiInputMixer, _rms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pcm(amplitude: int, n: int = 160) -> bytes:
    return struct.pack(f"{n}h", *([amplitude] * n))


def _frame(amplitude: int = 100, sample_rate: int = 16000) -> InputAudioRawFrame:
    return InputAudioRawFrame(
        audio=_pcm(amplitude),
        sample_rate=sample_rate,
        num_channels=1,
    )


# ---------------------------------------------------------------------------
# Unit tests for _rms helper
# ---------------------------------------------------------------------------

def test_rms_silent():
    assert _rms(_pcm(0)) == 0.0


def test_rms_nonzero():
    assert _rms(_pcm(100)) > 0


def test_rms_empty():
    assert _rms(b"") == 0.0


# ---------------------------------------------------------------------------
# Mixer selection logic (static method)
# ---------------------------------------------------------------------------

def test_select_both_none():
    assert MultiInputMixer._select(None, None) is None


def test_select_only_a():
    f = _frame(100)
    assert MultiInputMixer._select(f, None) is f


def test_select_only_b():
    f = _frame(100)
    assert MultiInputMixer._select(None, f) is f


def test_select_louder_wins():
    loud = _frame(500)
    quiet = _frame(10)
    assert MultiInputMixer._select(loud, quiet) is loud
    assert MultiInputMixer._select(quiet, loud) is loud


def test_select_equal_energy_returns_a():
    """When energies are equal, source a (native) wins."""
    a = _frame(100)
    b = _frame(100)
    assert MultiInputMixer._select(a, b) is a


# ---------------------------------------------------------------------------
# Integration: push → emit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_native_only_passthrough():
    """With only native frames, the mixer emits them immediately."""
    mixer = MultiInputMixer(window_ms=20)
    received: list[InputAudioRawFrame] = []

    async def emit(frame):
        received.append(frame)

    task = mixer.start(emit)
    # Push a native frame and wait one window + buffer
    f = _frame(200)
    mixer.push_native(f)
    await asyncio.sleep(0.06)
    mixer.stop()
    try:
        await asyncio.wait_for(task, timeout=0.5)
    except asyncio.CancelledError:
        pass

    assert len(received) >= 1
    # All emitted frames must be InputAudioRawFrame
    for r in received:
        assert isinstance(r, InputAudioRawFrame)


@pytest.mark.asyncio
async def test_louder_source_wins():
    """Mixer emits the louder source each window."""
    mixer = MultiInputMixer(window_ms=20)
    received: list[InputAudioRawFrame] = []

    async def emit(frame):
        received.append(frame)

    task = mixer.start(emit)

    loud = _frame(1000)
    quiet = _frame(5)

    # Push loud from webrtc, quiet from native repeatedly
    for _ in range(4):
        mixer.push_native(quiet)
        mixer.push_webrtc(loud)
        await asyncio.sleep(0.025)

    mixer.stop()
    try:
        await asyncio.wait_for(task, timeout=0.5)
    except asyncio.CancelledError:
        pass

    # All or nearly all emitted frames should have the loud amplitude
    loud_pcm = loud.audio
    loud_count = sum(1 for r in received if r.audio == loud_pcm)
    assert loud_count >= len(received) * 0.75, (
        f"Expected mostly loud frames, got {loud_count}/{len(received)}"
    )


@pytest.mark.asyncio
async def test_silent_window_emits_nothing():
    """If no frames are pushed in a window, nothing is emitted."""
    mixer = MultiInputMixer(window_ms=20)
    received: list[InputAudioRawFrame] = []

    async def emit(frame):
        received.append(frame)

    task = mixer.start(emit)
    # Just wait one window without pushing anything
    await asyncio.sleep(0.05)
    mixer.stop()
    try:
        await asyncio.wait_for(task, timeout=0.5)
    except asyncio.CancelledError:
        pass

    assert received == []


@pytest.mark.asyncio
async def test_stop_cleanly():
    """stop() must not raise and the task must finish."""
    mixer = MultiInputMixer(window_ms=20)

    async def emit(frame):
        pass

    task = mixer.start(emit)
    await asyncio.sleep(0.05)
    mixer.stop()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass
    # Task should be done
    assert task.done()
