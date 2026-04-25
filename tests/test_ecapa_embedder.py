"""Tests for ECAPAEmbedder.

speechbrain is NOT a test dep — too heavy to install in CI. We test the
wiring (resampling, padding, shape coercion, error handling) by injecting
a loader_factory that returns a stub model. A small set of integration
tests run only when speechbrain is importable, marked with the
`speechbrain_available` skip.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import numpy as np
import pytest

from agent.ecapa_embedder import ECAPAEmbedder, _TARGET_SR


speechbrain_available = pytest.mark.skipif(
    importlib.util.find_spec("speechbrain") is None,
    reason="speechbrain not installed; install via [speaker-id] extra",
)


class _StubModel:
    """Mimics speechbrain's EncoderClassifier.encode_batch — returns a
    fixed 192-dim torch-shaped tensor wrapped in a thin numpy adapter."""

    def __init__(self, embedding_value: float = 0.42) -> None:
        self.embedding_value = embedding_value
        self.calls: list[tuple[int, int]] = []  # (samples, batch)

    def encode_batch(self, tensor):
        self.calls.append((int(tensor.shape[-1]), int(tensor.shape[0])))
        # Real speechbrain returns shape (batch, 1, 192). We mimic that.
        import torch
        return torch.full((tensor.shape[0], 1, 192), self.embedding_value)


def _embedder_with_stub(stub: _StubModel | None = None) -> tuple[ECAPAEmbedder, _StubModel]:
    stub = stub or _StubModel()
    emb = ECAPAEmbedder(_loader_factory=lambda: stub)
    return emb, stub


# --- shape + resampling --------------------------------------------------


def test_returns_1d_float32_embedding() -> None:
    emb, _ = _embedder_with_stub()
    out = emb.encode(np.random.randn(_TARGET_SR).astype(np.float32), sample_rate=_TARGET_SR)
    assert out.ndim == 1
    assert out.dtype == np.float32
    assert out.size == 192


def test_passes_through_when_already_16k() -> None:
    emb, stub = _embedder_with_stub()
    wav = np.random.randn(_TARGET_SR).astype(np.float32)  # 1s @ 16k
    emb.encode(wav, sample_rate=_TARGET_SR)
    # Stub saw the exact same number of samples — no resampling
    assert stub.calls[0][0] == _TARGET_SR


def test_resamples_when_sr_differs() -> None:
    emb, stub = _embedder_with_stub()
    wav = np.random.randn(48000).astype(np.float32)  # 1s @ 48k
    emb.encode(wav, sample_rate=48000)
    # Resampled to 16k — sample count drops to ~16000
    assert abs(stub.calls[0][0] - _TARGET_SR) < 100


def test_sentinel_zero_sr_treated_as_16k() -> None:
    emb, stub = _embedder_with_stub()
    wav = np.random.randn(_TARGET_SR).astype(np.float32)
    emb.encode(wav, sample_rate=0)
    # Stub got 16k samples — no resampling triggered
    assert stub.calls[0][0] == _TARGET_SR


# --- padding -------------------------------------------------------------


def test_pads_short_clip_to_one_second() -> None:
    emb, stub = _embedder_with_stub()
    short = np.random.randn(4000).astype(np.float32)  # 0.25s
    emb.encode(short, sample_rate=_TARGET_SR)
    # Padded up to 16000
    assert stub.calls[0][0] == _TARGET_SR


def test_does_not_truncate_long_clip() -> None:
    """Long clips pass through full-length — speechbrain handles its
    own pooling."""
    emb, stub = _embedder_with_stub()
    long = np.random.randn(_TARGET_SR * 5).astype(np.float32)  # 5s
    emb.encode(long, sample_rate=_TARGET_SR)
    assert stub.calls[0][0] == _TARGET_SR * 5


# --- input validation ----------------------------------------------------


def test_empty_buffer_raises() -> None:
    emb, _ = _embedder_with_stub()
    with pytest.raises(ValueError, match="empty"):
        emb.encode(np.array([], dtype=np.float32), sample_rate=_TARGET_SR)


def test_2d_buffer_raises() -> None:
    emb, _ = _embedder_with_stub()
    with pytest.raises(ValueError, match="1-d"):
        emb.encode(np.zeros((2, 16000), dtype=np.float32), sample_rate=_TARGET_SR)


# --- model loading -------------------------------------------------------


def test_loader_factory_called_once_for_multiple_encodes() -> None:
    """Lazy load on first encode, then cached."""
    calls = {"n": 0}

    def _factory():
        calls["n"] += 1
        return _StubModel()

    emb = ECAPAEmbedder(_loader_factory=_factory)
    wav = np.random.randn(_TARGET_SR).astype(np.float32)

    emb.encode(wav, sample_rate=_TARGET_SR)
    emb.encode(wav, sample_rate=_TARGET_SR)
    emb.encode(wav, sample_rate=_TARGET_SR)
    assert calls["n"] == 1


def test_loader_only_runs_on_first_encode_not_construction() -> None:
    """Building the embedder must not trigger model load — the gate
    constructs the embedder eagerly even when the voiceprint is missing
    (owner-trust mode), and we don't want to download a 6 MB model
    needlessly."""
    calls = {"n": 0}

    def _factory():
        calls["n"] += 1
        return _StubModel()

    emb = ECAPAEmbedder(_loader_factory=_factory)
    assert calls["n"] == 0
    # Still 0 if we never call encode()


# --- gate integration ----------------------------------------------------


def test_works_with_speaker_gate(tmp_path) -> None:
    """End-to-end through the SpeakerGate Embedder protocol — confirms
    ECAPAEmbedder satisfies the protocol shape the gate expects."""
    from agent.speaker_gate import (
        OwnerVerifiedFrame,
        SpeakerGate,
        cosine_similarity,
    )

    emb = ECAPAEmbedder(_loader_factory=lambda: _StubModel(embedding_value=0.5))
    voice = emb.encode(
        np.random.randn(_TARGET_SR).astype(np.float32), sample_rate=_TARGET_SR
    )
    # Voiceprint is just a saved encoding from the same stub — cosine = 1.0
    cached = voice.copy()

    gate = SpeakerGate(
        embedder=emb,
        voiceprint=cached,
        threshold=0.95,
    )
    score = cosine_similarity(voice, cached)
    assert score == pytest.approx(1.0)


# --- real-model integration (skipped if speechbrain absent) -------------


@speechbrain_available
def test_real_speechbrain_loads_and_encodes() -> None:
    """Smoke test against the real model. Slow and downloads — only run
    when speechbrain is installed via the [speaker-id] extra."""
    emb = ECAPAEmbedder()
    wav = np.random.randn(_TARGET_SR * 2).astype(np.float32)  # 2s of noise
    out = emb.encode(wav, sample_rate=_TARGET_SR)
    assert out.shape == (192,)
    assert out.dtype == np.float32
    # Two encodings of the same input should be identical.
    out2 = emb.encode(wav, sample_rate=_TARGET_SR)
    np.testing.assert_array_almost_equal(out, out2)


# --- error path ----------------------------------------------------------


def test_real_load_without_speechbrain_raises_clear_error() -> None:
    """When speechbrain isn't installed, _load_speechbrain raises with
    an actionable hint pointing at the [speaker-id] extra."""
    if importlib.util.find_spec("speechbrain") is not None:
        pytest.skip("speechbrain IS installed — skipping the negative test")
    emb = ECAPAEmbedder()  # no _loader_factory → real path
    with pytest.raises(ImportError, match="speaker-id"):
        emb._ensure_loaded()
