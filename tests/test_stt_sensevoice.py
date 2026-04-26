"""Tests for SenseVoiceSTT (#66 Phase 2).

FunASR is NOT a test dep — too heavy. We test:
  - The tag parser as a pure function (covers every documented FunASR
    tag class + unknown / malformed cases).
  - The class flow with an injected stub model that returns canned
    tagged strings, covering the emit-order contract + speaker_verified
    tracking + decode failures.

A conditional integration test runs only when funasr is installable;
skipped otherwise so CI without the [sensevoice] extra still passes.
"""

from __future__ import annotations

import importlib.util
import io
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from pipecat.frames.frames import (
    ErrorFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from agent.frames import AudioEventFrame, EmotionFrame
from agent.speaker_gate import OwnerVerifiedFrame, StrangerDetectedFrame
from voice.stt_sensevoice import (
    SenseVoiceSTT,
    parse_sensevoice_output,
)


funasr_available = pytest.mark.skipif(
    importlib.util.find_spec("funasr") is None,
    reason="funasr not installed; install via [sensevoice] extra",
)


# --- parse_sensevoice_output ---------------------------------------------


def test_parse_full_tagged_output() -> None:
    """The standard FunASR shape — language, emotion, event, ITN marker,
    then the transcription text."""
    raw = "<|en|><|HAPPY|><|Speech|><|withitn|>Hello world"
    text, emotion, events, lang = parse_sensevoice_output(raw)
    assert text == "Hello world"
    assert emotion == "happy"
    assert events == []  # Speech is filtered out — tautology on a transcript
    assert lang == "en"


def test_parse_uppercase_emotion_normalizes() -> None:
    """FunASR emits emotion tags in UPPERCASE (HAPPY); the EmotionFrame
    contract is lowercase. The mapping is the load-bearing piece."""
    for tag, expected in [
        ("HAPPY", "happy"),
        ("SAD", "sad"),
        ("ANGRY", "angry"),
        ("NEUTRAL", "neutral"),
        ("FEARFUL", "fearful"),
        ("DISGUSTED", "disgusted"),
        ("SURPRISED", "surprised"),
    ]:
        raw = f"<|en|><|{tag}|>text"
        _, emotion, _, _ = parse_sensevoice_output(raw)
        assert emotion == expected


def test_parse_emo_unknown_falls_back_to_neutral() -> None:
    """FunASR's EMO_UNKNOWN means 'head was uncertain' — better to emit
    neutral than to surface an unknown label downstream."""
    _, emotion, _, _ = parse_sensevoice_output("<|en|><|EMO_UNKNOWN|>uhm")
    assert emotion == "neutral"


def test_parse_audio_events_collected() -> None:
    """BGM, Applause, Laughter, etc. are kept; Speech is filtered."""
    raw = "<|en|><|HAPPY|><|Speech|><|Laughter|><|BGM|>hi"
    _, _, events, _ = parse_sensevoice_output(raw)
    assert "Laughter" in events
    assert "BGM" in events
    assert "Speech" not in events


def test_parse_language_extracted() -> None:
    for lang_code in ("en", "zh", "ja", "ko", "yue"):
        raw = f"<|{lang_code}|><|NEUTRAL|>x"
        _, _, _, lang = parse_sensevoice_output(raw)
        assert lang == lang_code


def test_parse_strips_all_known_tags_from_text() -> None:
    """Even with multiple back-to-back tags, the transcription comes
    out clean."""
    raw = "<|en|><|HAPPY|><|Speech|><|withitn|>Hello, how are you doing today?"
    text, _, _, _ = parse_sensevoice_output(raw)
    assert text == "Hello, how are you doing today?"


def test_parse_unknown_tags_are_silent() -> None:
    """A future FunASR version that adds a new tag class shouldn't
    crash the parse — unknown tags are dropped from the text and
    don't change emotion / lang / events."""
    raw = "<|en|><|HAPPY|><|FUTURE_TAG_v2|>text"
    text, emotion, _, _ = parse_sensevoice_output(raw)
    assert text == "text"
    assert emotion == "happy"


def test_parse_empty_input() -> None:
    text, emotion, events, lang = parse_sensevoice_output("")
    assert text == ""
    assert emotion == "neutral"
    assert events == []
    assert lang == "en"


def test_parse_text_without_tags() -> None:
    """A FunASR call returning plain text (no tags) — defaults all the
    way through; transcription is the input verbatim."""
    text, emotion, events, lang = parse_sensevoice_output("just some text")
    assert text == "just some text"
    assert emotion == "neutral"
    assert events == []
    assert lang == "en"


def test_parse_only_itn_markers() -> None:
    """ITN flags carry no signal — they should disappear cleanly."""
    raw = "<|en|><|withitn|>$1.99"
    text, _, _, _ = parse_sensevoice_output(raw)
    assert text == "$1.99"


def test_parse_multiple_emotion_tags_uses_last() -> None:
    """If FunASR ever emits two emotion tags (shouldn't happen but
    defend against it), use whichever wins the final read."""
    raw = "<|en|><|NEUTRAL|>...<|HAPPY|>text"
    _, emotion, _, _ = parse_sensevoice_output(raw)
    assert emotion == "happy"


# --- SenseVoiceSTT class --------------------------------------------------


def _make_wav(duration_secs: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Build a real WAV blob — silence at the requested duration."""
    n = int(duration_secs * sample_rate)
    samples = np.zeros(n, dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class _StubModel:
    """In-place FunASR AutoModel. Returns a canned tagged string per
    invocation, recording call count for assertions."""

    def __init__(self, raw_text: str = "<|en|><|NEUTRAL|>hello") -> None:
        self.raw_text = raw_text
        self.calls: list[int] = []  # samples seen per call

    def generate(self, *, input, **kwargs):  # noqa: A002
        # input is a numpy array per our _invoke contract.
        self.calls.append(len(input))
        return [{"text": self.raw_text}]


@pytest.mark.asyncio
async def test_run_stt_emits_emotion_then_events_then_transcription() -> None:
    """The order contract from #66: EmotionFrame → AudioEventFrame
    (when present) → TranscriptionFrame. AudioTagsTap relies on this."""
    stub = _StubModel("<|en|><|HAPPY|><|Speech|><|Laughter|>Hello world")
    stt = SenseVoiceSTT(_loader_factory=lambda: stub)

    audio = _make_wav(2.0)
    frames = [f async for f in stt.run_stt(audio)]
    types = [type(f).__name__ for f in frames]
    assert types == ["EmotionFrame", "AudioEventFrame", "TranscriptionFrame"]

    emotion = frames[0]
    assert isinstance(emotion, EmotionFrame)
    assert emotion.emotion == "happy"
    assert emotion.lang == "en"
    assert emotion.audio_bytes == audio  # carry-through

    events = frames[1]
    assert isinstance(events, AudioEventFrame)
    assert "Laughter" in events.events

    transcript = frames[2]
    assert isinstance(transcript, TranscriptionFrame)
    assert transcript.text == "Hello world"


@pytest.mark.asyncio
async def test_run_stt_omits_audio_event_frame_when_no_events() -> None:
    """No non-Speech events detected → no AudioEventFrame at all,
    not an empty one. Sparse-by-design per the issue."""
    stub = _StubModel("<|en|><|NEUTRAL|><|Speech|>just talking")
    stt = SenseVoiceSTT(_loader_factory=lambda: stub)
    frames = [f async for f in stt.run_stt(_make_wav())]
    types = [type(f).__name__ for f in frames]
    assert types == ["EmotionFrame", "TranscriptionFrame"]


@pytest.mark.asyncio
async def test_run_stt_omits_transcription_when_empty() -> None:
    """SenseVoice can return tags + empty text on pure noise. Don't
    emit an empty TranscriptionFrame — downstream silently drops it
    in the existing code path; matches Whisper backend behavior."""
    stub = _StubModel("<|en|><|NEUTRAL|><|Speech|>")
    stt = SenseVoiceSTT(_loader_factory=lambda: stub)
    frames = [f async for f in stt.run_stt(_make_wav())]
    types = [type(f).__name__ for f in frames]
    assert types == ["EmotionFrame"]


@pytest.mark.asyncio
async def test_run_stt_owner_verified_default_true_before_speaker_gate() -> None:
    """Before any SpeakerGate frame arrives, the STT must default to
    owner-trust so first-utterance frames don't get incorrectly
    flagged as stranger. Matches the gate's own owner-trust contract."""
    stub = _StubModel("<|en|><|NEUTRAL|>x")
    stt = SenseVoiceSTT(_loader_factory=lambda: stub)
    frames = [f async for f in stt.run_stt(_make_wav())]
    emotion = next(f for f in frames if isinstance(f, EmotionFrame))
    assert emotion.speaker_verified is True


@pytest.mark.asyncio
async def test_speaker_verified_tracks_owner_verified_frame() -> None:
    stub = _StubModel("<|en|><|NEUTRAL|>x")
    stt = SenseVoiceSTT(_loader_factory=lambda: stub)
    # Stub the FrameProcessor base so process_frame can run without
    # the full pipeline-task registration step.
    pushed: list[Any] = []
    async def _capture(frame, direction=None):
        pushed.append(frame)
    stt.push_frame = _capture  # type: ignore[method-assign]

    # SpeakerGate fires StrangerDetectedFrame → STT remembers
    await stt.process_frame(StrangerDetectedFrame(score=0.3), FrameDirection.DOWNSTREAM)
    frames = [f async for f in stt.run_stt(_make_wav())]
    emotion = next(f for f in frames if isinstance(f, EmotionFrame))
    assert emotion.speaker_verified is False

    # Owner re-verifies on the next utterance
    await stt.process_frame(OwnerVerifiedFrame(score=0.85), FrameDirection.DOWNSTREAM)
    frames = [f async for f in stt.run_stt(_make_wav())]
    emotion = next(f for f in frames if isinstance(f, EmotionFrame))
    assert emotion.speaker_verified is True


@pytest.mark.asyncio
async def test_run_stt_handles_decode_failure() -> None:
    """Garbage bytes shouldn't crash the pipeline — yield ErrorFrame
    like the Whisper backend does."""
    stub = _StubModel()
    stt = SenseVoiceSTT(_loader_factory=lambda: stub)
    frames = [f async for f in stt.run_stt(b"not a wav")]
    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)
    # Stub model should NOT have been called — decode failed first.
    assert stub.calls == []


@pytest.mark.asyncio
async def test_run_stt_handles_inference_failure() -> None:
    """Exceptions inside the model invocation surface as ErrorFrame,
    not crash the FrameProcessor."""

    class _ExplodingModel:
        def generate(self, **kwargs):
            raise RuntimeError("simulated cuda OOM")

    stt = SenseVoiceSTT(_loader_factory=lambda: _ExplodingModel())
    frames = [f async for f in stt.run_stt(_make_wav())]
    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)
    assert "STT inference failed" in frames[0].error


@pytest.mark.asyncio
async def test_run_stt_resamples_non_16k_audio() -> None:
    """48k WebRTC paths → resample to 16k before invoking the model.
    The Whisper backend does the same; SenseVoice expects 16k mono."""
    stub = _StubModel("<|en|><|NEUTRAL|>x")
    stt = SenseVoiceSTT(_loader_factory=lambda: stub)

    n = 48000  # 1 second @ 48k
    samples = np.zeros(n, dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, samples, 48000, format="WAV", subtype="PCM_16")

    [f async for f in stt.run_stt(buf.getvalue())]
    # Stub saw the resampled 16k version, not 48k.
    assert len(stub.calls) == 1
    # Resampling is approximate — accept ±100 samples.
    assert abs(stub.calls[0] - 16000) < 100


def test_loader_factory_called_once() -> None:
    """Lazy load + cached on the instance."""
    calls = {"n": 0}

    def _factory():
        calls["n"] += 1
        return _StubModel()

    stt = SenseVoiceSTT(_loader_factory=_factory)
    stt._ensure_loaded()
    stt._ensure_loaded()
    stt._ensure_loaded()
    assert calls["n"] == 1


def test_real_load_without_funasr_raises_clear_error() -> None:
    """When funasr isn't installed, _load_funasr raises with a hint
    pointing at the [sensevoice] extra rather than a stack trace."""
    if importlib.util.find_spec("funasr") is not None:
        pytest.skip("funasr IS installed — skipping the negative test")
    stt = SenseVoiceSTT()
    with pytest.raises(ImportError, match="sensevoice"):
        stt._ensure_loaded()


# --- factory dispatch -----------------------------------------------------


def test_make_stt_dispatches_sensevoice(monkeypatch) -> None:
    """STT_BACKEND=sensevoice routes through SenseVoiceSTT."""
    monkeypatch.setenv("STT_BACKEND", "sensevoice")
    import importlib
    import voice.stt as stt_module
    importlib.reload(stt_module)
    # Build path returns a SenseVoiceSTT but constructing the real one
    # would trigger a load on first encode — we just check the type.
    from unittest.mock import patch
    with patch("voice.stt_sensevoice.SenseVoiceSTT.__init__", return_value=None):
        svc = stt_module.make_stt()
    from voice.stt_sensevoice import SenseVoiceSTT as _S
    assert isinstance(svc, _S)


# --- real-funasr smoke (skipped without the extra) ----------------------


@funasr_available
def test_real_funasr_loads_and_runs() -> None:
    """End-to-end against the actual SenseVoice model. Slow + downloads —
    only runs when [sensevoice] extra installed."""
    stt = SenseVoiceSTT()
    silence = np.zeros(16000, dtype=np.float32)
    raw = SenseVoiceSTT._invoke(stt._ensure_loaded(), silence)
    assert isinstance(raw, str)
