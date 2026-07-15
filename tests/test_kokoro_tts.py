"""Kokoro TTS off-event-loop synthesis (#481).

The regression these guard: Kokoro is synchronous torch. Iterating ``pipe()``
inline on the asyncio loop stalled the mic reader / VAD / SSE / barge-in for the
whole utterance. run_tts now pulls each chunk through a dedicated thread while
still *streaming* (yielding frames as they're produced). We prove both: frames
come out in order with the right shape, AND the synthesis ran off the loop.

No real Kokoro model loads here — ``_get_pipe`` is stubbed with a fake pipe that
yields KPipeline-style tuples, so these run in CI without the tensor download.
"""

from __future__ import annotations

import asyncio
import threading

import numpy as np
import pytest

import voice.tts.kokoro as k
from pipecat.frames.frames import TTSAudioRawFrame


def _fake_pipe_factory(chunks, record_threads=None):
    """Return a callable that mimics KPipeline: called with (text, voice, speed)
    and yields the given chunks, optionally recording the thread each ran on."""

    def fake_pipe(text, voice=None, speed=None):
        for c in chunks:
            if record_threads is not None:
                record_threads.append(threading.current_thread().name)
            yield c

    return fake_pipe


async def _drain(svc, text, ctx="ctx"):
    frames = []
    async for f in svc.run_tts(text, ctx):
        frames.append(f)
    return frames


def test_run_tts_streams_chunks_in_order_off_loop(monkeypatch):
    chunks = [
        ("gs", "ps", np.array([0.1, -0.2, 0.3], dtype=np.float32)),
        ("gs", "ps", np.array([0.5, -0.5], dtype=np.float32)),
    ]
    synth_threads: list[str] = []
    monkeypatch.setattr(
        k, "_get_pipe", lambda lang="a": _fake_pipe_factory(chunks, synth_threads)
    )
    svc = k.LocalKokoroTTS()

    async def go():
        loop_thread = threading.current_thread().name
        frames = await _drain(svc, "hello world")
        return loop_thread, frames

    loop_thread, frames = asyncio.run(go())

    # One audio frame per chunk, in order, with the right shape.
    assert len(frames) == 2
    assert all(isinstance(f, TTSAudioRawFrame) for f in frames)
    assert [len(f.audio) for f in frames] == [6, 4]  # int16 => 2 bytes/sample
    assert all(f.sample_rate == k.KOKORO_SR and f.num_channels == 1 for f in frames)

    # The whole point of #481: synthesis ran on the dedicated thread, never the
    # event-loop thread. Prevents a regression back to inline pipe() iteration.
    assert synth_threads, "fake pipe never ran"
    assert all(t.startswith("kokoro-tts") for t in synth_threads)
    assert loop_thread not in synth_threads


def test_run_tts_empty_text_yields_nothing(monkeypatch):
    called = False

    def _should_not_run(lang="a"):
        nonlocal called
        called = True
        return _fake_pipe_factory([])

    monkeypatch.setattr(k, "_get_pipe", _should_not_run)
    svc = k.LocalKokoroTTS()

    frames = asyncio.run(_drain(svc, "   "))
    assert frames == []
    assert not called, "empty text must short-circuit before touching the pipe"


def test_run_tts_skips_none_audio_chunks(monkeypatch):
    chunks = [
        ("gs", "ps", None),  # KPipeline can emit a token-only step with no audio
        ("gs", "ps", np.array([0.25], dtype=np.float32)),
    ]
    monkeypatch.setattr(k, "_get_pipe", lambda lang="a": _fake_pipe_factory(chunks))
    svc = k.LocalKokoroTTS()

    frames = asyncio.run(_drain(svc, "hi"))
    assert len(frames) == 1  # the None-audio step produced no frame
    assert frames[0].audio == np.int16(0.25 * 32767).tobytes()


def test_next_chunk_returns_sentinel_at_exhaustion():
    gen = (x for x in [1])
    assert k._next_chunk(gen) == 1
    # Exhausted → sentinel, so the run_tts loop stops without a StopIteration
    # crossing the executor boundary.
    assert k._next_chunk(gen) is k._GEN_DONE


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
