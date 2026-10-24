"""Turn-taking: bot-speaking frames, noise-proof start strategies, and the
interrupted flag in the spoken-text logger."""

from __future__ import annotations

import asyncio
import logging
import struct

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.turns.types import ProcessFrameResult

from agent.spoken_logger import SpokenTextLogger
from voice.local_transport import LocalAudioTransport
from voice.turn_start import (
    BotAwareVADUserTurnStartStrategy,
    barge_in_min_words,
    build_start_strategies,
)


# --- Bot*SpeakingFrame emission -------------------------------------------


def _output_proc():
    transport = LocalAudioTransport(sock_path="/tmp/orbis-audio-test.sock")
    proc = transport.output()
    sent: list = []
    pushed: list = []

    async def _pcm(audio, rate):
        sent.append(len(audio))

    async def _push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    transport._send_pcm = _pcm  # type: ignore[method-assign]
    proc.push_frame = _push  # type: ignore[method-assign]
    return transport, proc, sent, pushed


def _pcm(ms: int, rate: int = 24000) -> OutputAudioRawFrame:
    n = rate * ms // 1000
    return OutputAudioRawFrame(
        audio=struct.pack(f"<{n}h", *([0] * n)), sample_rate=rate, num_channels=1
    )


@pytest.mark.asyncio
async def test_bot_started_once_then_stopped_after_playout(monkeypatch):
    transport, proc, sent, pushed = _output_proc()
    monkeypatch.setattr(proc, "_TAIL_S", 0.0)
    for _ in range(3):
        await proc.process_frame(_pcm(20), FrameDirection.DOWNSTREAM)
    started = [f for f in pushed if isinstance(f, BotStartedSpeakingFrame)]
    assert len(started) == 2  # broadcast: one upstream + one downstream copy
    assert transport.bot_speaking is True
    assert len(sent) == 3
    await asyncio.sleep(0.2)  # 60ms of audio has drained
    stopped = [f for f in pushed if isinstance(f, BotStoppedSpeakingFrame)]
    assert len(stopped) == 2
    assert transport.bot_speaking is False


@pytest.mark.asyncio
async def test_playout_extends_while_audio_keeps_arriving(monkeypatch):
    _, proc, _, pushed = _output_proc()
    monkeypatch.setattr(proc, "_TAIL_S", 0.0)
    await proc.process_frame(_pcm(300), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.1)
    # Audio is written faster than real time: still "speaking" mid-playout.
    assert not [f for f in pushed if isinstance(f, BotStoppedSpeakingFrame)]
    assert proc.bot_speaking
    await proc._end_speaking("test")


@pytest.mark.asyncio
async def test_interruption_stops_bot_speaking_immediately():
    transport, proc, _, pushed = _output_proc()
    await proc.process_frame(_pcm(2000), FrameDirection.DOWNSTREAM)
    assert transport.bot_speaking
    await proc.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    assert not transport.bot_speaking
    assert any(isinstance(f, BotStoppedSpeakingFrame) for f in pushed)
    assert proc._stop_task is None


# --- start strategies -------------------------------------------------------


def _wire(strategy):
    started: list = []

    async def _on_started(*a, **kw):
        started.append(1)

    strategy.trigger_user_turn_started = _on_started  # type: ignore[method-assign]
    return started


def _vad():
    return VADUserStartedSpeakingFrame(start_secs=0.2, timestamp=0)


@pytest.mark.asyncio
async def test_vad_starts_turn_when_bot_idle():
    s = BotAwareVADUserTurnStartStrategy()
    started = _wire(s)
    res = await s.process_frame(_vad())
    assert res == ProcessFrameResult.STOP and started


@pytest.mark.asyncio
async def test_vad_blip_ignored_while_bot_speaking():
    s = BotAwareVADUserTurnStartStrategy()
    started = _wire(s)
    await s.process_frame(BotStartedSpeakingFrame())
    res = await s.process_frame(_vad())
    assert res == ProcessFrameResult.CONTINUE and not started
    await s.process_frame(BotStoppedSpeakingFrame())
    await s.process_frame(_vad())
    assert started


@pytest.mark.asyncio
async def test_vad_blip_ignored_while_response_pending():
    s = BotAwareVADUserTurnStartStrategy()
    started = _wire(s)
    await s.handle_user_turn_stopped()  # bot is now thinking
    await s.process_frame(_vad())
    assert not started


def test_build_start_strategies_default_and_disable(monkeypatch):
    monkeypatch.delenv("BARGE_IN_MIN_WORDS", raising=False)
    assert barge_in_min_words() == 2
    strategies = build_start_strategies()
    assert strategies is not None and len(strategies) == 2
    assert strategies[1]._min_words == 2
    monkeypatch.setenv("BARGE_IN_MIN_WORDS", "0")
    assert build_start_strategies() is None
    monkeypatch.setenv("BARGE_IN_MIN_WORDS", "junk")
    assert barge_in_min_words() == 2


@pytest.mark.asyncio
async def test_min_words_gates_interrupt_while_bot_speaking():
    strategies = build_start_strategies(min_words=2)
    assert strategies is not None
    min_words = strategies[1]
    started = _wire(min_words)

    async def _noop():
        return None

    min_words.trigger_reset_aggregation = _noop  # type: ignore[method-assign]
    await min_words.process_frame(BotStartedSpeakingFrame())
    await min_words.process_frame(
        TranscriptionFrame(text="uh", user_id="u", timestamp="t")
    )
    assert not started
    await min_words.process_frame(
        TranscriptionFrame(text="wait stop", user_id="u", timestamp="t")
    )
    assert started


# --- spoken logger ----------------------------------------------------------


async def _feed(logger_proc, frames):
    async def _push(frame, direction=FrameDirection.DOWNSTREAM):
        return None

    logger_proc.push_frame = _push  # type: ignore[method-assign]
    for f in frames:
        await logger_proc.process_frame(f, FrameDirection.DOWNSTREAM)


@pytest.mark.asyncio
async def test_spoken_logger_flags_interrupted(caplog):
    proc = SpokenTextLogger()
    with caplog.at_level(logging.INFO, logger="agent.spoken_logger"):
        await _feed(
            proc,
            [
                LLMTextFrame("I am sending"),
                LLMTextFrame(" it now and"),
                InterruptionFrame(),
            ],
        )
    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        "interrupted after 23 chars" in m and "interrupted=true" in m for m in msgs
    )
    assert proc._llm_buf == []  # next response starts clean


@pytest.mark.asyncio
async def test_spoken_logger_logs_chars_on_complete_response(caplog):
    proc = SpokenTextLogger()
    with caplog.at_level(logging.INFO, logger="agent.spoken_logger"):
        await _feed(proc, [LLMTextFrame("Hello there."), LLMFullResponseEndFrame()])
    msgs = [r.getMessage() for r in caplog.records]
    assert any("llm chars=12" in m and "interrupted" not in m for m in msgs)
