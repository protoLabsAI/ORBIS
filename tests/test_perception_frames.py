"""Tests for the perception-layer frame contracts (#66 Phase 1).

Foundation only: frame types + audio_context_block prompt content.
Doesn't exercise SenseVoiceSTT (lands in Phase 2) or AudioTagsTap
(Phase 3) — those need the model dep + integration tests.
"""

from __future__ import annotations

from agent.frames import (
    AudioEventFrame,
    CONFIDENCE_BUCKETS,
    EMOTION_LABELS,
    EmotionFrame,
)
from agent.filler import audio_context_block


def test_emotion_labels_match_sensevoice_taxonomy() -> None:
    """The seven labels SenseVoice produces. Locked here so a future
    swap to a different STT can't silently change downstream consumers
    (mood-delta map, audio_context_block, etc.) without an explicit
    update to the constant."""
    assert set(EMOTION_LABELS) == {
        "neutral",
        "happy",
        "sad",
        "angry",
        "fearful",
        "disgusted",
        "surprised",
    }


def test_confidence_buckets_are_three_tier() -> None:
    """Coarse-grained because FunASR doesn't expose token logits.
    high/medium/low gives downstream taps something to gate on without
    pretending to a precision we don't have."""
    assert CONFIDENCE_BUCKETS == ("high", "medium", "low")


def test_emotion_frame_defaults_safe() -> None:
    """An EmotionFrame() with no args must not crash any consumer —
    defaults are picked so the worst case is "no emotion signal,
    proceed normally"."""
    f = EmotionFrame()
    assert f.emotion == "neutral"
    assert f.confidence == "medium"
    assert f.lang == "en"
    assert f.speaker_verified is True
    assert f.audio_bytes == b""


def test_emotion_frame_carries_audio_bytes() -> None:
    """audio_bytes lets v5-side taps re-infer on the same buffer
    instead of paralleling the STT capture."""
    pcm = bytes(range(256))
    f = EmotionFrame(emotion="happy", audio_bytes=pcm, lang="en")
    assert f.audio_bytes == pcm
    assert f.emotion == "happy"


def test_emotion_frame_speaker_verified_propagates_speaker_gate() -> None:
    """The flag mirrors the last SpeakerGate decision — AudioTagsTap
    uses it to gate mood writes (only owner audio nudges mood)."""
    owner = EmotionFrame(speaker_verified=True)
    stranger = EmotionFrame(speaker_verified=False)
    assert owner.speaker_verified is True
    assert stranger.speaker_verified is False


def test_audio_event_frame_starts_empty() -> None:
    f = AudioEventFrame()
    assert f.events == []


def test_audio_event_frame_carries_events() -> None:
    f = AudioEventFrame(events=["Laughter", "BGM"])
    assert "Laughter" in f.events
    assert "BGM" in f.events


def test_audio_event_frames_have_independent_default_lists() -> None:
    """Common dataclass footgun: a default mutable arg shared across
    instances. field(default_factory=list) prevents this; pin it
    here so a future "simplification" can't reintroduce the bug."""
    a = AudioEventFrame()
    b = AudioEventFrame()
    a.events.append("Laughter")
    assert b.events == []


# --- audio_context_block (system prompt) ---------------------------------


def test_audio_context_block_has_required_sections() -> None:
    """The block's job is to teach the LLM what the [audio]
    annotation means and how to use it. Pin the load-bearing pieces."""
    text = audio_context_block()
    # Header
    assert "AUDIO CONTEXT" in text
    # The exact prefix the AudioTagsTap will inject
    assert "[audio]" in text
    # The shape of the annotation (sample line)
    assert "emotion=" in text
    assert "speaker=" in text
    # Don't-parrot rule — load-bearing UX
    assert "DO NOT" in text or "don't" in text.lower() or "Do not" in text
    # Missing-fields fallback
    assert "missing" in text.lower() or "ignore" in text.lower()


def test_audio_context_block_is_static_no_args() -> None:
    """Doesn't take verbosity / tts_backend — same advice regardless
    of persona configuration. Pinned so future evolution stays
    explicit."""
    a = audio_context_block()
    b = audio_context_block()
    assert a == b


def test_audio_context_block_warns_against_parroting() -> None:
    """The 'I can tell you sound happy!' anti-pattern is the most
    common LLM failure on this signal — make sure the block says so."""
    text = audio_context_block().lower()
    assert "parrot" in text or "happy" in text
