"""ECAPA-TDNN speaker embedder for the speaker-verification gate.

Wraps speechbrain's ``spkrec-ecapa-voxceleb`` (~6 M params, 192-dim
embedding) into the ``Embedder`` Protocol from ``agent.speaker_gate``.
Lazy import keeps the speechbrain + torchaudio deps optional — install
via ``pip install -e ".[speaker-id]"`` when wiring this in.

References:
- Issue #35 spec: https://github.com/protoLabsAI/ORBIS/issues/35
- Model card: https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- Companion-stack research: protoLabsAI/protoLab → experiments/companion-stack
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ECAPA's training and inference SR. Anything else gets resampled.
_TARGET_SR = 16000

# HF model id; pinned so a future hub upload doesn't silently shift
# verification scores.
_DEFAULT_MODEL_SOURCE = os.environ.get(
    "SPEAKER_ID_MODEL", "speechbrain/spkrec-ecapa-voxceleb"
)


def _default_savedir() -> Path:
    """Where the downloaded model lands. Reuses the HF home set in
    agent/paths.py so the desktop app + Docker image share one cache."""
    hf_home = os.environ.get("HF_HOME") or os.environ.get(
        "TRANSFORMERS_CACHE", str(Path.home() / ".cache" / "huggingface")
    )
    return Path(hf_home) / "speechbrain" / "spkrec-ecapa-voxceleb"


# Process-wide model cache so multiple gates / sessions share the load.
# Keyed by (source, device) so an upgrade to a different model doesn't
# return the old one. Locked because torch model construction isn't
# thread-safe.
_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_MODEL_LOCK = threading.Lock()


class ECAPAEmbedder:
    """speechbrain-backed speaker embedder.

    Constructor only saves config — the model loads lazily on the first
    ``encode()`` call so import / construction stays cheap. Supports
    overriding the underlying loader via ``_loader_factory`` for tests.
    """

    def __init__(
        self,
        *,
        source: str = _DEFAULT_MODEL_SOURCE,
        savedir: str | Path | None = None,
        device: str | None = None,
        _loader_factory: Any = None,
    ) -> None:
        self._source = source
        self._savedir = Path(savedir) if savedir else _default_savedir()
        # device=None → speechbrain auto-picks (cuda → mps → cpu in
        # recent versions). Override via env or constructor arg when
        # explicit control is wanted.
        self._device = device or os.environ.get("SPEAKER_ID_DEVICE")
        self._loader_factory = _loader_factory  # tests inject; None = real
        self._model: Any = None

    def encode(self, wav: np.ndarray, sample_rate: int) -> np.ndarray:
        """Return a 1-d float32 embedding for the given mono wav.

        Resamples to 16 kHz if needed (soxr is already a project dep
        for STT). Pads short clips to 1 second so the encoder gets
        enough context — ECAPA's frame-level convolutions need a
        minimum window.

        ``sample_rate=0`` is a sentinel meaning "unknown, use 16000".
        Real callers should pass the transport's actual SR.
        """
        if wav.size == 0:
            raise ValueError("encode() called with empty wav buffer")
        if wav.ndim != 1:
            raise ValueError(f"wav must be 1-d mono; got shape {wav.shape}")

        sr = sample_rate or _TARGET_SR
        if sr != _TARGET_SR:
            wav = self._resample(wav, sr, _TARGET_SR)

        # Pad to at least 1s so ECAPA has enough frames. Padding silence
        # at the start preserves embedding stability — silent prefix
        # contributes minimally to the pooled output.
        min_samples = _TARGET_SR
        if wav.size < min_samples:
            pad = np.zeros(min_samples - wav.size, dtype=np.float32)
            wav = np.concatenate([pad, wav])

        model = self._ensure_loaded()
        emb = self._run(model, wav)
        # Defensive — speechbrain returns torch.Tensor; flatten to 1-d
        # numpy for downstream cosine helper.
        flat = np.asarray(emb, dtype=np.float32).reshape(-1)
        return flat

    # --- internals ----------------------------------------------------

    @staticmethod
    def _resample(wav: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
        import soxr  # already a project dep
        return soxr.resample(wav, src_sr, dst_sr).astype(np.float32, copy=False)

    def _ensure_loaded(self) -> Any:
        if self._model is not None:
            return self._model
        if self._loader_factory is not None:
            self._model = self._loader_factory()
            return self._model
        cache_key = (self._source, self._device or "auto")
        with _MODEL_LOCK:
            cached = _MODEL_CACHE.get(cache_key)
            if cached is not None:
                self._model = cached
                return cached
            self._model = self._load_speechbrain()
            _MODEL_CACHE[cache_key] = self._model
        return self._model

    def _load_speechbrain(self) -> Any:
        """Import + load the real model. Errors are surfaced loud — a
        misconfigured speaker-gate is the wrong place to silently
        fall back to passthrough; the SpeakerGate's outer try/except
        already handles encode() failures by trusting the owner.
        """
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError as e:
            raise ImportError(
                "speechbrain is required for ECAPAEmbedder; install via "
                "`pip install -e \".[speaker-id]\"` (or skip enrollment "
                "to keep the gate in owner-trust mode)"
            ) from e
        self._savedir.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {
            "source": self._source,
            "savedir": str(self._savedir),
        }
        if self._device:
            kwargs["run_opts"] = {"device": self._device}
        logger.info(
            f"[ecapa] loading {self._source} from {self._savedir} "
            f"(device={self._device or 'auto'})"
        )
        return EncoderClassifier.from_hparams(**kwargs)

    @staticmethod
    def _run(model: Any, wav: np.ndarray) -> np.ndarray:
        """Drive the speechbrain model. Wrapped so tests can stub the
        whole thing without depending on torch at import time.

        Forces wav to a contiguous numpy buffer before torch.from_numpy.
        soxr.resample is contiguous in practice but the API doesn't
        guarantee it; torch.from_numpy raises on non-contiguous input.
        Cheap one-liner that prevents a hard-to-reproduce crash on
        future soxr / numpy upgrades."""
        import torch
        with torch.no_grad():
            wav = np.ascontiguousarray(wav)
            tensor = torch.from_numpy(wav).unsqueeze(0)  # (1, samples)
            emb = model.encode_batch(tensor)  # (1, 1, 192)
            return emb.detach().cpu().numpy()
