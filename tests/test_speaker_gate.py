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
    regardless of input. Records each call's (wav_bytes, sample_rate)
    so tests can assert SR forwarding (the bug-2 regression)."""

    def __init__(self, vector: np.ndarray) -> None:
        self.vector = vector
        self.calls: list[tuple[int, int]] = []  # (wav_size, sample_rate)

    def encode(self, wav: np.ndarray, sample_rate: int) -> np.ndarray:
        self.calls.append((int(wav.size), int(sample_rate)))
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

    assert len(embedder.calls) == 2
    # Each call sees its own utterance's bytes (800 samples = 800 ints from
    # int16 PCM → 800 floats post-decode), not the cumulative buffer.
    assert embedder.calls[0][0] == 800
    assert embedder.calls[1][0] == 800


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


# --- design-review regression tests ---------------------------------------


@pytest.mark.asyncio
async def test_audio_outside_utterance_is_not_buffered(captured_gate) -> None:
    """Bug 1 regression: audio frames arriving without a preceding
    UserStartedSpeakingFrame must not be buffered. A degenerate VAD
    state with two consecutive Stopped frames previously encoded stale
    audio from the prior turn."""
    owner_vec = np.array([1.0, 0.0], dtype=np.float32)
    embedder = _MockEmbedder(owner_vec)
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=embedder,
        threshold=0.5,
    )
    # Real utterance — buffered correctly.
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(samples=800), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert len(embedder.calls) == 1

    # Spurious audio frames between utterances (echo-guard tail, etc.) —
    # must NOT accumulate.
    await gate.process_frame(_audio_frame(samples=800), FrameDirection.DOWNSTREAM)
    await gate.process_frame(_audio_frame(samples=800), FrameDirection.DOWNSTREAM)

    # Second Stopped without a Started first — degenerate VAD. With the
    # _speaking guard, this no-ops; pre-fix it would have encoded the
    # spurious frames as if they were a real turn.
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert len(embedder.calls) == 1, \
        "Stopped without preceding Started must not trigger encode"


@pytest.mark.asyncio
async def test_sample_rate_forwarded_to_embedder(captured_gate) -> None:
    """Bug 2 regression: gate must capture frame.sample_rate and forward
    it to encode(). Pre-fix it passed sample_rate=0, which the embedder
    silently treated as 16k — feeding 48k PCM as 16k yields garbage
    embeddings and (worse) the trust-fallback would promote a stranger
    to OwnerVerifiedFrame."""
    owner_vec = np.array([1.0, 0.0], dtype=np.float32)
    embedder = _MockEmbedder(owner_vec)
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=embedder,
        threshold=0.5,
    )
    # WebRTC default is 48000; force that.
    audio_48k = InputAudioRawFrame(
        audio=np.zeros(800, dtype=np.int16).tobytes(),
        sample_rate=48000,
        num_channels=1,
    )

    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(audio_48k, FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    assert len(embedder.calls) == 1
    _wav_size, captured_sr = embedder.calls[0]
    assert captured_sr == 48000, \
        "embedder must receive the actual transport sample rate"


@pytest.mark.asyncio
async def test_first_frame_pins_sample_rate_for_utterance(captured_gate) -> None:
    """Sample rate is captured from the FIRST audio frame each utterance
    and pinned for the rest. Avoids averaging if a buggy transport
    flipped SR mid-utterance."""
    owner_vec = np.array([1.0, 0.0], dtype=np.float32)
    embedder = _MockEmbedder(owner_vec)
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=embedder,
        threshold=0.5,
    )
    f48k = InputAudioRawFrame(
        audio=np.zeros(400, dtype=np.int16).tobytes(),
        sample_rate=48000,
        num_channels=1,
    )
    f16k = InputAudioRawFrame(
        audio=np.zeros(400, dtype=np.int16).tobytes(),
        sample_rate=16000,
        num_channels=1,
    )

    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(f48k, FrameDirection.DOWNSTREAM)  # pins SR=48k
    await gate.process_frame(f16k, FrameDirection.DOWNSTREAM)  # ignored for SR
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    assert embedder.calls[0][1] == 48000


@pytest.mark.asyncio
async def test_stranger_frame_carries_enum_not_string(captured_gate) -> None:
    """Bug 3 regression: StrangerDetectedFrame.action is the
    StrangerAction enum, not a raw string. Downstream consumers can
    switch on the enum without re-parsing."""
    owner_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    stranger_vec = np.array([0.0, 1.0, 0.0], dtype=np.float32)
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
    assert isinstance(strangers[0].action, StrangerAction)
    assert strangers[0].action is StrangerAction.DELEGATE_GUEST
    # Enum-as-str inheritance means string equality still works for any
    # legacy consumer that compares to the literal.
    assert strangers[0].action == "delegate_guest"


@pytest.mark.asyncio
async def test_upstream_audio_does_not_buffer(captured_gate) -> None:
    """An InputAudioRawFrame travelling UPSTREAM (control reverse path,
    bug, etc.) must not enter the verification buffer. The gate sits on
    the downstream input path; only DOWNSTREAM frames matter."""
    owner_vec = np.array([1.0, 0.0], dtype=np.float32)
    embedder = _MockEmbedder(owner_vec)
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=embedder,
        threshold=0.5,
    )
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    # Spurious upstream audio frame — must be ignored.
    await gate.process_frame(_audio_frame(samples=400), FrameDirection.UPSTREAM)
    # Real downstream audio.
    await gate.process_frame(_audio_frame(samples=800), FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    # Only the 800-sample DOWNSTREAM frame entered the buffer. 400 from
    # upstream did not.
    assert len(embedder.calls) == 1
    assert embedder.calls[0][0] == 800


@pytest.mark.asyncio
async def test_upstream_started_stopped_does_not_arm_or_fire(captured_gate) -> None:
    """Started/Stopped going upstream must not flip _speaking or trigger
    a verify pass. Otherwise an out-of-band reflux could fire false
    StrangerDetectedFrames."""
    owner_vec = np.array([1.0, 0.0], dtype=np.float32)
    embedder = _MockEmbedder(owner_vec)
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=embedder,
        threshold=0.5,
    )
    # All three frames travelling upstream — gate should be inert.
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await gate.process_frame(_audio_frame(), FrameDirection.UPSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.UPSTREAM)

    verified = [f for f, _ in gate.pushed if isinstance(f, OwnerVerifiedFrame)]
    strangers = [f for f, _ in gate.pushed if isinstance(f, StrangerDetectedFrame)]
    assert verified == []
    assert strangers == []
    assert len(embedder.calls) == 0


@pytest.mark.asyncio
async def test_multi_channel_audio_is_buffered_as_is(captured_gate) -> None:
    """Multi-channel raw bytes flow through without channel handling.
    Pinned as a known limitation: the gate doesn't deinterleave L+R
    samples — production transports negotiate mono so this rarely fires.
    Test exists to flag if/when transport configuration changes."""
    owner_vec = np.array([1.0, 0.0], dtype=np.float32)
    embedder = _MockEmbedder(owner_vec)
    gate = captured_gate(
        voiceprint=owner_vec,
        embedder=embedder,
        threshold=0.5,
    )
    stereo = InputAudioRawFrame(
        audio=np.zeros(1600, dtype=np.int16).tobytes(),  # 800 samples × 2 ch
        sample_rate=16000,
        num_channels=2,
    )
    await gate.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await gate.process_frame(stereo, FrameDirection.DOWNSTREAM)
    await gate.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    # Currently encoded as interleaved samples — test pins the behavior
    # so any future change is intentional. Real fix: detect num_channels
    # and downmix; out of scope for the foundation.
    assert len(embedder.calls) == 1
    assert embedder.calls[0][0] == 1600  # interleaved sample count
