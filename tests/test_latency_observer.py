"""Tests for LatencyObserver (L5) — the per-stage TTFB/TTFA logger."""
from __future__ import annotations

import logging

import pytest
from pipecat.frames.frames import MetricsFrame, TextFrame
from pipecat.metrics.metrics import TTFAMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from agent.latency import LatencyObserver


def _pushed(frame):
    return FramePushed(
        source=None,
        destination=None,
        frame=frame,
        direction=FrameDirection.DOWNSTREAM,
        timestamp=0,
    )


def _latency_lines(caplog):
    return [r.getMessage() for r in caplog.records if "[latency]" in r.getMessage()]


@pytest.mark.asyncio
async def test_logs_ttfb_and_ttfa(caplog):
    obs = LatencyObserver()
    frame = MetricsFrame(
        data=[
            TTFBMetricsData(processor="LLMService", value=0.32),
            TTFAMetricsData(
                processor="KokoroTTS", ttfa=0.21, ttfb=0.18, leading_silence=0.03
            ),
        ]
    )
    with caplog.at_level(logging.INFO, logger="agent.latency"):
        await obs.on_push_frame(_pushed(frame))
    lines = _latency_lines(caplog)
    assert any("LLMService ttfb=0.320s" in ln for ln in lines)
    assert any("KokoroTTS ttfa=0.210s" in ln for ln in lines)


@pytest.mark.asyncio
async def test_dedupes_same_frame(caplog):
    obs = LatencyObserver()
    frame = MetricsFrame(data=[TTFBMetricsData(processor="X", value=0.1)])
    with caplog.at_level(logging.INFO, logger="agent.latency"):
        await obs.on_push_frame(_pushed(frame))
        await obs.on_push_frame(_pushed(frame))  # same frame id → logged once
    assert len(_latency_lines(caplog)) == 1


@pytest.mark.asyncio
async def test_ignores_non_metrics_frames(caplog):
    obs = LatencyObserver()
    with caplog.at_level(logging.INFO, logger="agent.latency"):
        await obs.on_push_frame(_pushed(TextFrame("hello")))
    assert _latency_lines(caplog) == []
