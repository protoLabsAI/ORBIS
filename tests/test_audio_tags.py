"""Tests for AudioTagsTap (#66 Phase 3).

The tap subscribes to perception frames + writes mood deltas + injects
the `[audio]` system message. We test:

  - Frame contracts: which inputs trigger which outputs
  - Mood-delta map matches the spec table in #66
  - Owner-vs-stranger gating (only owner audio nudges mood)
  - LLMMessagesAppendFrame format + ordering vs TranscriptionFrame
  - Disabled / no-emotion / passthrough invariants
  - drift_mood failures don't blow up the frame loop

A fake `mem` records mood writes so we can assert exact deltas.
Pipecat's TranscriptionFrame is built with the public API — no
mocking required.
"""

from __future__ import annotations

from typing import Any

import pytest
from pipecat.frames.frames import (
    Frame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from agent.audio_tags import (
    AudioTagsTap,
    _EMOTION_DELTAS,
    _audio_annotation,
    make_audio_tags_tap,
)
from agent.frames import AudioEventFrame, EmotionFrame
from agent.speaker_gate import OwnerVerifiedFrame, StrangerDetectedFrame


# --- delta map matches #66 spec ------------------------------------------


@pytest.mark.parametrize("emotion,expected", [
    ("happy",     (+0.10, +0.05)),
    ("surprised", ( 0.00, +0.10)),
    ("neutral",   ( 0.00,  0.00)),
    ("sad",       (-0.10, -0.05)),
    ("fearful",   (-0.05, +0.10)),
    ("angry",     (-0.15, +0.15)),
    ("disgusted", (-0.10, +0.05)),
])
def test_emotion_delta_map_matches_issue_66_spec(emotion, expected) -> None:
    """The map is the load-bearing UX call from #66 — pinning each
    entry so a future 'just tweak one' edit shows up in PR review."""
    assert _EMOTION_DELTAS[emotion] == expected


def test_delta_map_covers_full_emotion_taxonomy() -> None:
    """Every label SenseVoice emits has a row. EMOTION_LABELS is the
    pinned constant from agent/frames.py."""
    from agent.frames import EMOTION_LABELS
    for label in EMOTION_LABELS:
        assert label in _EMOTION_DELTAS, \
            f"{label!r} missing from _EMOTION_DELTAS — silent skip risk"


# --- _audio_annotation ----------------------------------------------------


def test_annotation_renders_owner_emotion_lang() -> None:
    f = EmotionFrame(emotion="happy", lang="en", speaker_verified=True)
    assert _audio_annotation(f, []) == "[audio] emotion=happy lang=en speaker=owner"


def test_annotation_renders_stranger() -> None:
    f = EmotionFrame(emotion="sad", lang="en", speaker_verified=False)
    assert _audio_annotation(f, []) == "[audio] emotion=sad lang=en speaker=stranger"


def test_annotation_includes_events_when_present() -> None:
    f = EmotionFrame(emotion="happy", lang="en", speaker_verified=True)
    out = _audio_annotation(f, ["Laughter", "BGM"])
    assert "events=Laughter,BGM" in out
    # Order preserved.
    assert out.endswith("events=Laughter,BGM")


def test_annotation_omits_events_field_when_empty() -> None:
    """Empty events shouldn't render `events=` — the LLM doesn't need
    the noise. audio_context_block tells it to ignore missing fields."""
    f = EmotionFrame(emotion="neutral", lang="en", speaker_verified=True)
    out = _audio_annotation(f, [])
    assert "events=" not in out


def test_annotation_returns_none_when_no_emotion() -> None:
    """Pre-utterance state with no EmotionFrame ever received → don't
    inject a misleading partial annotation."""
    assert _audio_annotation(None, []) is None
    assert _audio_annotation(None, ["Laughter"]) is None


# --- AudioTagsTap harness -------------------------------------------------


class _FakeMood:
    """Records mood writes so tests can assert exact deltas."""

    def __init__(self) -> None:
        self.calls: list[dict[str, float]] = []

    def drift_mood(self, **kwargs) -> None:
        # Only record non-None values so the assertions read naturally.
        self.calls.append({k: v for k, v in kwargs.items() if v is not None})


class _FakeMem:
    def __init__(self) -> None:
        self.personality = _FakeMood()


@pytest.fixture
def captured_tap():
    """Build an AudioTagsTap that captures push_frame calls so tests
    can assert which frames were emitted downstream."""
    pushed: list[tuple[Frame, Any]] = []

    def _build(enabled: bool = True, mem: Any = None) -> AudioTagsTap:
        mem = mem if mem is not None else _FakeMem()
        tap = AudioTagsTap(mem=mem, enabled=enabled)

        async def _capture(frame: Frame, direction=None) -> None:
            pushed.append((frame, direction))

        tap.push_frame = _capture  # type: ignore[method-assign]
        tap.pushed = pushed  # type: ignore[attr-defined]
        tap.fake_mem = mem  # type: ignore[attr-defined]
        return tap

    return _build


# --- mood writes ---------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_happy_emotion_writes_mood_delta(captured_tap) -> None:
    tap = captured_tap()
    f = EmotionFrame(emotion="happy", lang="en", speaker_verified=True)
    await tap.process_frame(f, FrameDirection.DOWNSTREAM)
    assert tap.fake_mem.personality.calls == [
        {"valence_delta": +0.10, "arousal_delta": +0.05},
    ]


@pytest.mark.asyncio
async def test_owner_angry_emotion_writes_mood_delta(captured_tap) -> None:
    tap = captured_tap()
    f = EmotionFrame(emotion="angry", lang="en", speaker_verified=True)
    await tap.process_frame(f, FrameDirection.DOWNSTREAM)
    assert tap.fake_mem.personality.calls == [
        {"valence_delta": -0.15, "arousal_delta": +0.15},
    ]


@pytest.mark.asyncio
async def test_stranger_does_not_write_mood(captured_tap) -> None:
    """Owner-only mood writes — strangers must NOT nudge the owner's
    mood register. This is the load-bearing reason EmotionFrame
    carries speaker_verified."""
    tap = captured_tap()
    f = EmotionFrame(emotion="happy", lang="en", speaker_verified=False)
    await tap.process_frame(f, FrameDirection.DOWNSTREAM)
    assert tap.fake_mem.personality.calls == []


@pytest.mark.asyncio
async def test_neutral_emotion_does_not_write_mood(captured_tap) -> None:
    """Neutral has zero deltas — skip the drift_mood call entirely
    (drift_mood would no-op too, but the early skip avoids the round-
    trip on the per-turn hot path)."""
    tap = captured_tap()
    f = EmotionFrame(emotion="neutral", lang="en", speaker_verified=True)
    await tap.process_frame(f, FrameDirection.DOWNSTREAM)
    assert tap.fake_mem.personality.calls == []


@pytest.mark.asyncio
async def test_unknown_emotion_does_not_write_mood(captured_tap, caplog) -> None:
    """If a future SenseVoice version emits an emotion not in
    _EMOTION_DELTAS, skip the write rather than guessing. Log debug
    so it's traceable but not noisy."""
    tap = captured_tap()
    f = EmotionFrame(emotion="confused", lang="en", speaker_verified=True)
    await tap.process_frame(f, FrameDirection.DOWNSTREAM)
    assert tap.fake_mem.personality.calls == []


@pytest.mark.asyncio
async def test_drift_mood_failure_does_not_break_pipeline(captured_tap) -> None:
    """A memory write that raises (DB locked, disk full) must not
    blow up the frame loop — the user keeps talking."""

    class _BrokenMood:
        def drift_mood(self, **kwargs):
            raise RuntimeError("simulated DB error")

    class _BrokenMem:
        def __init__(self):
            self.personality = _BrokenMood()

    tap = captured_tap(mem=_BrokenMem())
    f = EmotionFrame(emotion="happy", lang="en", speaker_verified=True)
    # Should not raise.
    await tap.process_frame(f, FrameDirection.DOWNSTREAM)
    # Frame still passes through.
    assert any(isinstance(p, EmotionFrame) for p, _ in tap.pushed)


# --- [audio] annotation injection -----------------------------------------


@pytest.mark.asyncio
async def test_audio_annotation_injected_before_transcription(captured_tap) -> None:
    """Per #66: SystemFrame BEFORE TranscriptionFrame so the LLM
    sees audio context first. We use LLMMessagesAppendFrame with
    role=system to land it in the LLM context."""
    tap = captured_tap()
    e = EmotionFrame(emotion="happy", lang="en", speaker_verified=True)
    t = TranscriptionFrame("hello there", "user", "2026-04-26T00:00:00Z")

    await tap.process_frame(e, FrameDirection.DOWNSTREAM)
    await tap.process_frame(t, FrameDirection.DOWNSTREAM)

    # Pulled the EmotionFrame, then the LLMMessagesAppendFrame, then
    # the TranscriptionFrame.
    types = [type(f).__name__ for f, _ in tap.pushed]
    assert types == [
        "EmotionFrame",
        "LLMMessagesAppendFrame",
        "TranscriptionFrame",
    ]


@pytest.mark.asyncio
async def test_audio_annotation_carries_system_role(captured_tap) -> None:
    tap = captured_tap()
    e = EmotionFrame(emotion="sad", lang="en", speaker_verified=True)
    t = TranscriptionFrame("...", "user", "2026-04-26T00:00:00Z")

    await tap.process_frame(e, FrameDirection.DOWNSTREAM)
    await tap.process_frame(t, FrameDirection.DOWNSTREAM)

    append = next(p for p, _ in tap.pushed if isinstance(p, LLMMessagesAppendFrame))
    assert len(append.messages) == 1
    assert append.messages[0]["role"] == "system"
    assert append.messages[0]["content"].startswith("[audio]")
    assert "emotion=sad" in append.messages[0]["content"]
    # run_llm=False — the TranscriptionFrame that follows fires the run
    assert append.run_llm is False


@pytest.mark.asyncio
async def test_audio_event_frame_appears_in_annotation(captured_tap) -> None:
    """Order from SenseVoice: Emotion → AudioEvent → Transcription.
    The annotation injected at Transcription time should include the
    events from the in-between AudioEventFrame."""
    tap = captured_tap()
    e = EmotionFrame(emotion="happy", lang="en", speaker_verified=True)
    a = AudioEventFrame(events=["Laughter"])
    t = TranscriptionFrame("hi", "user", "2026-04-26T00:00:00Z")

    await tap.process_frame(e, FrameDirection.DOWNSTREAM)
    await tap.process_frame(a, FrameDirection.DOWNSTREAM)
    await tap.process_frame(t, FrameDirection.DOWNSTREAM)

    append = next(p for p, _ in tap.pushed if isinstance(p, LLMMessagesAppendFrame))
    assert "events=Laughter" in append.messages[0]["content"]


@pytest.mark.asyncio
async def test_events_reset_between_utterances(captured_tap) -> None:
    """AudioEventFrame is per-utterance; a Laughter on turn 1 must
    NOT appear in the annotation for turn 2."""
    tap = captured_tap()

    # Utterance 1 — Laughter present.
    await tap.process_frame(
        EmotionFrame(emotion="happy", speaker_verified=True),
        FrameDirection.DOWNSTREAM,
    )
    await tap.process_frame(AudioEventFrame(events=["Laughter"]), FrameDirection.DOWNSTREAM)
    await tap.process_frame(
        TranscriptionFrame("ha ha", "u", "2026-04-26T00:00:00Z"),
        FrameDirection.DOWNSTREAM,
    )

    # Utterance 2 — no AudioEventFrame at all this turn (sparse).
    await tap.process_frame(
        EmotionFrame(emotion="neutral", speaker_verified=True),
        FrameDirection.DOWNSTREAM,
    )
    await tap.process_frame(
        TranscriptionFrame("what time is it", "u", "2026-04-26T00:00:00Z"),
        FrameDirection.DOWNSTREAM,
    )

    appends = [p for p, _ in tap.pushed if isinstance(p, LLMMessagesAppendFrame)]
    assert len(appends) == 2
    assert "events=Laughter" in appends[0].messages[0]["content"]
    assert "events=" not in appends[1].messages[0]["content"]


@pytest.mark.asyncio
async def test_no_annotation_without_emotion(captured_tap) -> None:
    """If a TranscriptionFrame arrives without a preceding EmotionFrame
    (e.g. STT_BACKEND=local — no SenseVoice in the pipeline),
    don't inject a stale or empty annotation."""
    tap = captured_tap()
    t = TranscriptionFrame("hello", "user", "2026-04-26T00:00:00Z")
    await tap.process_frame(t, FrameDirection.DOWNSTREAM)

    appends = [p for p, _ in tap.pushed if isinstance(p, LLMMessagesAppendFrame)]
    assert appends == []


# --- speaker_verified updates from gate frames -------------------------


@pytest.mark.asyncio
async def test_stranger_detected_frame_flips_emotion_speaker_flag(captured_tap) -> None:
    """The latest EmotionFrame's speaker_verified is mirrored to
    the latest gate decision. Lets late-arriving SpeakerGate frames
    correct the EmotionFrame's flag before the [audio] line emits."""
    tap = captured_tap()
    await tap.process_frame(
        EmotionFrame(emotion="happy", speaker_verified=True),
        FrameDirection.DOWNSTREAM,
    )
    await tap.process_frame(
        StrangerDetectedFrame(score=0.3),
        FrameDirection.DOWNSTREAM,
    )
    await tap.process_frame(
        TranscriptionFrame("hi", "u", "2026-04-26T00:00:00Z"),
        FrameDirection.DOWNSTREAM,
    )

    append = next(p for p, _ in tap.pushed if isinstance(p, LLMMessagesAppendFrame))
    assert "speaker=stranger" in append.messages[0]["content"]


@pytest.mark.asyncio
async def test_owner_verified_frame_re_flips_to_owner(captured_tap) -> None:
    tap = captured_tap()
    await tap.process_frame(
        EmotionFrame(emotion="happy", speaker_verified=False),
        FrameDirection.DOWNSTREAM,
    )
    await tap.process_frame(
        OwnerVerifiedFrame(score=0.85),
        FrameDirection.DOWNSTREAM,
    )
    await tap.process_frame(
        TranscriptionFrame("hi", "u", "2026-04-26T00:00:00Z"),
        FrameDirection.DOWNSTREAM,
    )

    append = next(p for p, _ in tap.pushed if isinstance(p, LLMMessagesAppendFrame))
    assert "speaker=owner" in append.messages[0]["content"]


# --- enabled / direction gates -------------------------------------------


@pytest.mark.asyncio
async def test_disabled_tap_is_passthrough(captured_tap) -> None:
    tap = captured_tap(enabled=False)
    e = EmotionFrame(emotion="happy", speaker_verified=True)
    t = TranscriptionFrame("hi", "u", "2026-04-26T00:00:00Z")

    await tap.process_frame(e, FrameDirection.DOWNSTREAM)
    await tap.process_frame(t, FrameDirection.DOWNSTREAM)

    types = [type(f).__name__ for f, _ in tap.pushed]
    # Original frames flow through; no LLMMessagesAppendFrame injection
    assert "LLMMessagesAppendFrame" not in types
    assert types.count("EmotionFrame") == 1
    assert types.count("TranscriptionFrame") == 1
    # No mood writes either.
    assert tap.fake_mem.personality.calls == []


@pytest.mark.asyncio
async def test_upstream_frames_pass_through(captured_tap) -> None:
    """UPSTREAM control frames must not trigger mood writes or
    annotation injection."""
    tap = captured_tap()
    await tap.process_frame(
        EmotionFrame(emotion="happy", speaker_verified=True),
        FrameDirection.UPSTREAM,
    )
    assert tap.fake_mem.personality.calls == []


# --- factory ---------------------------------------------------------------


def test_make_audio_tags_tap_default_enabled(monkeypatch) -> None:
    monkeypatch.delenv("AUDIO_TAGS", raising=False)
    tap = make_audio_tags_tap(mem=_FakeMem())
    assert tap._enabled is True


@pytest.mark.parametrize("flag", ["off", "OFF", "0", "false", "no"])
def test_make_audio_tags_tap_disabled_via_env(monkeypatch, flag) -> None:
    monkeypatch.setenv("AUDIO_TAGS", flag)
    tap = make_audio_tags_tap(mem=_FakeMem())
    assert tap._enabled is False


@pytest.mark.parametrize("flag", ["on", "1", "true", "yes", ""])
def test_make_audio_tags_tap_enabled_via_env(monkeypatch, flag) -> None:
    monkeypatch.setenv("AUDIO_TAGS", flag)
    tap = make_audio_tags_tap(mem=_FakeMem())
    assert tap._enabled is True
