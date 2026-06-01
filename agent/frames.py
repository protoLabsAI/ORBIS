"""Cross-cutting frame types for the perception layer.

These frames carry signal that more than one module reads. Today:

  - ``EmotionFrame`` — emitted once per utterance by the STT layer
    (when ``STT_BACKEND=sensevoice``). Consumed by ``AudioTagsTap``
    for per-turn mood writes and by the system-prompt
    ``audio_context_block`` for the in-prompt ``[audio]`` annotation.

  - ``AudioEventFrame`` — non-speech events detected during the
    utterance (laughter, BGM, applause, …). Sparse — only emitted
    when something is present.

The speaker-verification frames (``OwnerVerifiedFrame``,
``StrangerDetectedFrame``) live in ``agent/speaker_gate.py`` next to
the gate that produces them; they predate this module. New
cross-cutting frames land here.

References:
- Issue #66 (SenseVoice perception layer)
- docs/internal/voice-lifecycle-research.md § audio-pre
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pipecat.frames.frames import Frame


# Emotion taxonomy — SenseVoice's 7-class output. The frame carries
# the string directly so consumers can switch on either the literal
# or a named constant without coupling to a specific enum import.
EMOTION_LABELS = (
    "neutral",
    "happy",
    "sad",
    "angry",
    "fearful",
    "disgusted",
    "surprised",
)

# Confidence buckets — coarse-grained because SenseVoice (via FunASR)
# doesn't expose per-token logits cleanly. Until we have a way to
# compute calibrated confidence, ``medium`` is the operational default.
CONFIDENCE_BUCKETS = ("high", "medium", "low")


@dataclass
class EmotionFrame(Frame):
    """Per-utterance emotion + language detection from the STT layer.

    Emitted BEFORE ``TranscriptionFrame`` each utterance so consumers
    that gate on emotion (e.g. AudioTagsTap mood writer) have the
    signal in hand before the transcript reaches the LLM.

    Carries ``audio_bytes`` so downstream taps that want to re-infer
    on the same audio (e.g. the v5 SNR / environment / speaking-rate
    heads) don't need a parallel buffer. Empty bytes ``b\"\"`` is
    legal — taps that need audio must guard accordingly.
    """

    emotion: str = "neutral"
    confidence: str = "medium"
    lang: str = "en"
    speaker_verified: bool = True  # mirrors the last SpeakerGate decision
    audio_bytes: bytes = b""


@dataclass
class AudioEventFrame(Frame):
    """Non-speech events detected during the utterance window.

    Sparse: emitted only when at least one event is present. Common
    values from SenseVoice include ``Laughter``, ``BGM``, ``Applause``.
    """

    events: list[str] = field(default_factory=list)
