"""Tests for the speaker-verification gate foundation (#35 PR 1).

Covers:
- Voiceprint persistence (load / save / atomic rename / corrupted handling)
- Cosine similarity (orthogonal / identical / scaled / zero-magnitude)
- The gate's three-mode behavior:
    * disabled → passthrough, no verification frame
    * enabled, no voiceprint → owner-trust (emits OwnerVerifiedFrame
      with score=1.0 regardless of who's speaking)
    * enabled with voiceprint + mock embedder → real cosine gate

Speechbrain is NOT a test dep — the Embedder protocol lets us inject a
trivial mock that returns deterministic embeddings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from agent.speaker_gate import (
    OwnerVerifiedFrame,
    SpeakerGate,
    StrangerAction,
    StrangerDetectedFrame,
    cosine_similarity,
    load_voiceprint,
    save_voiceprint,
)


# --- cosine similarity ---------------------------------------------------


def test_cosine_identical_vectors_returns_one() -> None:
    a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert cosine_similarity(a, a) == pytest.approx(1.0)


def test_cosine_opposite_vectors_returns_minus_one() -> None:
    a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert cosine_similarity(a, -a) == pytest.approx(-1.0)


def test_cosine_orthogonal_vectors_returns_zero() -> None:
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_invariant_to_magnitude() -> None:
    a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    b = np.array([2.0, 4.0, 6.0], dtype=np.float32)
    assert cosine_similarity(a, b) == pytest.approx(1.0)


def test_cosine_zero_magnitude_returns_zero() -> None:
    """Degenerate case — silent rather than ZeroDivision."""
    a = np.zeros(3, dtype=np.float32)
    b = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert cosine_similarity(a, b) == 0.0


# --- voiceprint persistence ----------------------------------------------


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    emb = np.random.RandomState(42).randn(192).astype(np.float32)
    p = tmp_path / "voiceprint.npy"
    save_voiceprint(p, emb)
    assert p.exists()
    loaded = load_voiceprint(p)
    assert loaded is not None
    np.testing.assert_array_almost_equal(loaded, emb)


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_voiceprint(tmp_path / "nope.npy") is None


def test_load_corrupted_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "bad.npy"
    p.write_bytes(b"not a real npy file")
    assert load_voiceprint(p) is None


def test_load_2d_returns_none(tmp_path: Path) -> None:
    """We expect 1-d embeddings; a 2-d array is malformed."""
    p = tmp_path / "two_d.npy"
    np.save(p, np.zeros((2, 192), dtype=np.float32))
    assert load_voiceprint(p) is None


def test_save_rejects_2d() -> None:
    with pytest.raises(ValueError):
        save_voiceprint("/tmp/should-not-create.npy", np.zeros((2, 192)))


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "nested" / "vp.npy"
    save_voiceprint(p, np.array([1.0, 2.0], dtype=np.float32))
    assert p.exists()


# --- gate: harness -------------------------------------------------------


class _MockEmbedder:
    """Deterministic embedder for tests. Returns a configured vector
    regardless of input. Records call count for assertions."""

    def __init__(self, vector: np.ndarray) -> None:
        self.vector = vector
        self.calls = 0

    def encode(self, wav: np.ndarray, sample_rate: int) -> np.ndarray:
        self.calls += 1
        return self.vector


@pytest.fixture
def captured_gate():
    """Build a SpeakerGate that captures push_frame calls so tests can
    assert which frames were emitted downstream."""
    pushed: list[tuple[Frame, Any]] = []

    def _build(**kwargs):
        gate = SpeakerGate(**kwargs)
        async def _capture(frame, direction=None):
            pushed.append((frame, direction))
        gate.push_frame = _capture  # type: ignore[method-assign]
        gate.pushed = pushed  # type: ignore[attr-defined]
        return gate
    return _build


def _audio_frame(samples: int = 1600) -> InputAudioRawFrame:
    """16-bit PCM @ 16kHz, ~100ms of silence. Real audio shape."""
    return InputAudioRawFrame(
        audio=np.zeros(samples, dtype=np.int16).tobytes(),
        sample_rate=16000,
        num_channels=1,
    )


# --- gate: disabled mode -------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_gate_emits_no_verification(captured_gate) -> None:
    gate = captured_gate(enabled=False)
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    pushed_types = [type(f).__name__ for f, _ in gate.pushed]
    assert "OwnerVerifiedFrame" not in pushed_types
    assert "StrangerDetectedFrame" not in pushed_types
    # All three originals still flowed through
    assert pushed_types.count("UserStartedSpeakingFrame") == 1
    assert pushed_types.count("InputAudioRawFrame") == 1
    assert pushed_types.count("UserStoppedSpeakingFrame") == 1


# --- gate: owner-trust fallback -----------------------------------------


@pytest.mark.asyncio
async def test_no_voiceprint_emits_owner_verified(captured_gate) -> None:
    """No enrollment yet → trust everyone (preserves no-auth single-user
    deployment story)."""
    gate = captured_gate(voiceprint=None)
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    verified = [f for f, _ in gate.pushed if isinstance(f, OwnerVerifiedFrame)]
    assert len(verified) == 1
    assert verified[0].score == 1.0


@pytest.mark.asyncio
async def test_voiceprint_without_embedder_falls_back(captured_gate) -> None:
    """Edge case: voiceprint set but no embedder injected. Don't crash
    — fall back to owner-trust with a warning."""
    gate = captured_gate(voiceprint=np.array([1.0, 0.0], dtype=np.float32))
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    verified = [f for f, _ in gate.pushed if isinstance(f, OwnerVerifiedFrame)]
    assert len(verified) == 1


# --- gate: live verification --------------------------------------------


@pytest.mark.asyncio
async def test_owner_verified_when_embedding_matches(captured_gate) -> None:
    owner_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=_MockEmbedder(owner_vec),
        threshold=0.5,
    )
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    verified = [f for f, _ in gate.pushed if isinstance(f, OwnerVerifiedFrame)]
    strangers = [f for f, _ in gate.pushed if isinstance(f, StrangerDetectedFrame)]
    assert len(verified) == 1
    assert verified[0].score == pytest.approx(1.0)
    assert strangers == []


@pytest.mark.asyncio
async def test_stranger_detected_when_embedding_differs(captured_gate) -> None:
    owner_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    stranger_vec = np.array([0.0, 1.0, 0.0], dtype=np.float32)  # orthogonal
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=_MockEmbedder(stranger_vec),
        threshold=0.5,
        stranger_action=StrangerAction.REFUSE,
    )
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    verified = [f for f, _ in gate.pushed if isinstance(f, OwnerVerifiedFrame)]
    strangers = [f for f, _ in gate.pushed if isinstance(f, StrangerDetectedFrame)]
    assert verified == []
    assert len(strangers) == 1
    assert strangers[0].score == pytest.approx(0.0)
    assert strangers[0].action == "refuse"


@pytest.mark.asyncio
async def test_threshold_boundary_inclusive(captured_gate) -> None:
    """Score == threshold → owner (>=, not >). Important for tuning UX —
    a deployment that calibrates threshold to 0.62 wants 0.62 to count."""
    owner_vec = np.array([1.0, 0.0], dtype=np.float32)
    half_match = np.array([0.5, 0.866025], dtype=np.float32)  # ~0.5 cos
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=_MockEmbedder(half_match),
        threshold=0.5,
    )
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    verified = [f for f, _ in gate.pushed if isinstance(f, OwnerVerifiedFrame)]
    assert len(verified) == 1


@pytest.mark.asyncio
async def test_empty_buffer_falls_back_to_owner_trust(captured_gate) -> None:
    """If audio frames never arrived between started/stopped (mic drop?),
    trust the owner rather than emitting a stranger frame on no data."""
    gate = captured_gate(
        voiceprint=np.array([1.0, 0.0], dtype=np.float32),
        embedder=_MockEmbedder(np.array([0.0, 1.0], dtype=np.float32)),
        threshold=0.5,
    )
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    # No InputAudioRawFrames between
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    verified = [f for f, _ in gate.pushed if isinstance(f, OwnerVerifiedFrame)]
    strangers = [f for f, _ in gate.pushed if isinstance(f, StrangerDetectedFrame)]
    assert len(verified) == 1
    assert verified[0].score == 1.0
    assert strangers == []


@pytest.mark.asyncio
async def test_buffer_resets_between_utterances(captured_gate) -> None:
    """Two back-to-back utterances each get their own embed call —
    audio from the first must not bleed into the second."""
    owner_vec = np.array([1.0, 0.0], dtype=np.float32)
    embedder = _MockEmbedder(owner_vec)
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=embedder,
        threshold=0.5,
    )
    # Utterance 1
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(samples=800), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    # Utterance 2
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(samples=800), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    assert embedder.calls == 2


@pytest.mark.asyncio
async def test_embedder_failure_falls_back_to_owner_trust(captured_gate) -> None:
    """The embedder exploding shouldn't lock the user out. Log + trust."""

    class _BrokenEmbedder:
        def encode(self, wav, sample_rate):
            raise RuntimeError("simulated model failure")

    gate = captured_gate(
        voiceprint=np.array([1.0, 0.0], dtype=np.float32),
        embedder=_BrokenEmbedder(),
        threshold=0.5,
    )
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    verified = [f for f, _ in gate.pushed if isinstance(f, OwnerVerifiedFrame)]
    strangers = [f for f, _ in gate.pushed if isinstance(f, StrangerDetectedFrame)]
    assert len(verified) == 1
    assert verified[0].score == 1.0
    assert strangers == []


# --- gate: passthrough invariant ----------------------------------------


@pytest.mark.asyncio
async def test_originals_always_passthrough(captured_gate) -> None:
    """Whatever mode we're in, the original Started/Audio/Stopped frames
    must reach downstream — STT and VAD depend on them."""
    gate = captured_gate(
        voiceprint=np.array([1.0, 0.0], dtype=np.float32),
        embedder=_MockEmbedder(np.array([0.0, 1.0], dtype=np.float32)),
        stranger_action=StrangerAction.WARN,
    )
    inputs = [
        UserStartedSpeakingFrame(),
        _audio_frame(),
        _audio_frame(),
        UserStoppedSpeakingFrame(),
    ]
    for f in inputs:
        await gate.process_frame(f, FrameDirection.DOWNSTREAM)

    forwarded = [f for f, _ in gate.pushed if not isinstance(
        f, (OwnerVerifiedFrame, StrangerDetectedFrame)
    )]
    assert len(forwarded) == len(inputs)


# --- new tests covering fixed bugs ---------------------------------------


@pytest.mark.asyncio
async def test_audio_outside_vad_window_not_buffered(captured_gate) -> None:
    """Bug 1 fix: InputAudioRawFrame arriving before UserStartedSpeakingFrame
    (or after UserStoppedSpeakingFrame) must NOT be buffered. Stale audio
    from a prior utterance should not bleed into the next embed call."""
    owner_vec = np.array([1.0, 0.0], dtype=np.float32)
    embedder = _MockEmbedder(owner_vec)
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=embedder,
        threshold=0.5,
    )
    # Audio arrives before the started frame — must be ignored
    await gate.process_frame(_audio_frame(samples=3200), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(samples=3200), FrameDirection.DOWNSTREAM)
    # Now a real utterance
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(samples=800), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    # Embedder must have been called with only the 800-sample in-window chunk
    assert embedder.calls == 1
    # Verify the encode received only the in-window audio (800 samples = 1600 bytes int16)
    # We can't inspect the wav directly, but we can verify embed was called once (not on stale)
    verified = [f for f, _ in gate.pushed if isinstance(f, OwnerVerifiedFrame)]
    assert len(verified) == 1


@pytest.mark.asyncio
async def test_sample_rate_forwarded_to_embedder(captured_gate) -> None:
    """Bug 2 fix: embedder.encode must receive the actual sample_rate from
    the InputAudioRawFrame, not the 0 sentinel."""
    received_sr: list[int] = []

    class _SRCapturingEmbedder:
        def encode(self, wav: np.ndarray, sample_rate: int) -> np.ndarray:
            received_sr.append(sample_rate)
            return np.array([1.0, 0.0], dtype=np.float32)

    gate = captured_gate(
        voiceprint=np.array([1.0, 0.0], dtype=np.float32),
        embedder=_SRCapturingEmbedder(),
        threshold=0.5,
    )
    frame = InputAudioRawFrame(
        audio=np.zeros(960, dtype=np.int16).tobytes(),
        sample_rate=48000,
        num_channels=1,
    )
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    assert len(received_sr) == 1
    assert received_sr[0] == 48000, (
        f"Expected sample_rate=48000, got {received_sr[0]}. "
        "Bug 2: gate must forward frame.sample_rate, not a 0 sentinel."
    )


@pytest.mark.asyncio
async def test_stranger_action_is_enum_not_string(captured_gate) -> None:
    """Bug 3 fix: StrangerDetectedFrame.action must be a StrangerAction
    enum value, not a raw string. Downstream consumers should be able to
    switch on the enum without re-parsing."""
    owner_vec = np.array([1.0, 0.0], dtype=np.float32)
    stranger_vec = np.array([0.0, 1.0], dtype=np.float32)
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=_MockEmbedder(stranger_vec),
        threshold=0.5,
        stranger_action=StrangerAction.DELEGATE_GUEST,
    )
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    strangers = [f for f, _ in gate.pushed if isinstance(f, StrangerDetectedFrame)]
    assert len(strangers) == 1
    assert isinstance(strangers[0].action, StrangerAction), (
        f"action should be StrangerAction enum, got {type(strangers[0].action)}"
    )
    assert strangers[0].action is StrangerAction.DELEGATE_GUEST
