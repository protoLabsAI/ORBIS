"""Tests for TeeOutputProcessor."""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from pipecat.frames.frames import (
    CancelFrame,
    DataFrame,
    EndFrame,
    OutputAudioRawFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from voice.tee_processor import TeeOutputProcessor


def _make_audio_frame(n_samples: int = 160) -> OutputAudioRawFrame:
    pcm = struct.pack(f"{n_samples}h", *([100] * n_samples))
    return OutputAudioRawFrame(audio=pcm, sample_rate=16000, num_channels=1)


def _make_sink() -> AsyncMock:
    sink = AsyncMock()
    sink.write_audio_frame = AsyncMock(return_value=True)
    return sink


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _collect_pushed(tee: TeeOutputProcessor, frame, direction=FrameDirection.DOWNSTREAM):
    """Feed one frame into tee and collect everything push_frame emits."""
    pushed = []

    async def _capture(f, d=FrameDirection.DOWNSTREAM):
        pushed.append(f)

    tee.push_frame = _capture
    await tee.process_frame(frame, direction)
    return pushed


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audio_frame_fans_to_all_sinks():
    """OutputAudioRawFrame reaches every registered sink."""
    sinks = [_make_sink(), _make_sink(), _make_sink()]
    tee = TeeOutputProcessor(sinks=sinks)

    frame = _make_audio_frame()
    await _collect_pushed(tee, frame)

    for sink in sinks:
        sink.write_audio_frame.assert_awaited_once_with(frame)


@pytest.mark.asyncio
async def test_audio_frame_also_pushed_downstream():
    """After fanning to sinks the frame must still propagate downstream."""
    sink = _make_sink()
    tee = TeeOutputProcessor(sinks=[sink])

    frame = _make_audio_frame()
    pushed = await _collect_pushed(tee, frame)

    assert frame in pushed


@pytest.mark.asyncio
async def test_zero_sinks_no_error():
    """With no sinks registered the processor must not raise."""
    tee = TeeOutputProcessor()
    frame = _make_audio_frame()
    # Should not raise
    pushed = await _collect_pushed(tee, frame)
    assert frame in pushed


@pytest.mark.asyncio
async def test_add_and_remove_sink():
    """Dynamically added sinks receive frames; removed sinks do not."""
    tee = TeeOutputProcessor()
    sink_a = _make_sink()
    sink_b = _make_sink()

    await tee.add_sink(sink_a)
    await tee.add_sink(sink_b)
    frame1 = _make_audio_frame()
    await _collect_pushed(tee, frame1)

    await tee.remove_sink(sink_a)
    frame2 = _make_audio_frame(200)
    await _collect_pushed(tee, frame2)

    # sink_a only saw frame1
    assert sink_a.write_audio_frame.await_count == 1
    # sink_b saw both
    assert sink_b.write_audio_frame.await_count == 2


@pytest.mark.asyncio
async def test_non_audio_frame_passes_through():
    """TextFrame and other non-audio frames must pass through unchanged."""
    sink = _make_sink()
    tee = TeeOutputProcessor(sinks=[sink])

    frame = TextFrame("hello")
    pushed = await _collect_pushed(tee, frame)

    assert frame in pushed
    sink.write_audio_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_end_frame_propagates_without_raising():
    """EndFrame must propagate downstream; sinks are not called with EndFrame."""
    sink = _make_sink()
    tee = TeeOutputProcessor(sinks=[sink])

    frame = EndFrame()
    pushed = await _collect_pushed(tee, frame)

    assert frame in pushed
    sink.write_audio_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_sink_failure_does_not_break_other_sinks():
    """A failing sink must not prevent healthy sinks from receiving the frame."""
    bad_sink = _make_sink()
    bad_sink.write_audio_frame = AsyncMock(side_effect=RuntimeError("boom"))
    good_sink = _make_sink()

    tee = TeeOutputProcessor(sinks=[bad_sink, good_sink])
    frame = _make_audio_frame()
    # Must not raise
    await _collect_pushed(tee, frame)

    good_sink.write_audio_frame.assert_awaited_once_with(frame)


@pytest.mark.asyncio
async def test_add_duplicate_sink_ignored():
    """Adding the same sink twice must not double-deliver frames."""
    sink = _make_sink()
    tee = TeeOutputProcessor(sinks=[sink])
    await tee.add_sink(sink)  # duplicate

    assert await tee.sink_count() == 1
    frame = _make_audio_frame()
    await _collect_pushed(tee, frame)
    sink.write_audio_frame.assert_awaited_once()
