#!/usr/bin/env python3
"""openWakeWord reference pipeline — the oracle the Rust detector matches.

The Rust wake-word detector (src-tauri/src/audio/wake_word.rs, via the pure-Rust
`tract` ONNX runtime) must reproduce this byte-for-byte. Run this against a wav
(or the built-in deterministic inputs) to get the score Rust should produce.

Pipeline (openWakeWord, 3 ONNX models in sequence — all shipped by the picker
into the app-data models/ dir):

  16 kHz int16-range audio
    → melspectrogram.onnx   (internal STFT: hop=160/10ms, win=640/40ms)
        → squeeze → /10 + 2     (the oWW normalization — easy to miss)
    → 76-frame mel window, step 8  → embedding_model.onnx → 96-dim embedding
    → last 16 embeddings [1,16,96] → <wake>.onnx → sigmoid score 0..1

We use a **full-window recompute** (melspec the whole ~2 s ring each tick)
rather than oWW's incremental per-chunk mel buffering: the models are tiny, it
runs comfortably at the 80 ms cadence, and it sidesteps the STFT
chunk-boundary-overlap bug a naive streaming port would hit. ~31360 samples
(1.96 s) yield the first 16 embeddings → one score.

Audio is fed as int16-range float32 (NOT normalized to [-1,1]) — the melspec
model was trained on that range.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np
import onnxruntime as ort

MEL_WINDOW = 76  # mel frames per embedding
MEL_STEP = 8  # frame hop between embeddings (= 1280 samples = 80 ms)
WAKE_WINDOW = 16  # embeddings per wake score
HOP = 160  # melspec internal hop (samples)


def _models_dir() -> Path:
    import os

    base = os.environ.get("ORBIS_MODELS_DIR") or "models"
    return Path(base).expanduser() / "wakeword"


class WakeReference:
    def __init__(self, wake_model: str = "hey_orbis", models_dir: Path | None = None):
        d = models_dir or _models_dir()
        p = lambda n: str(d / n)  # noqa: E731
        self._mel = ort.InferenceSession(p("melspectrogram.onnx"), providers=["CPUExecutionProvider"])
        self._emb = ort.InferenceSession(p("embedding_model.onnx"), providers=["CPUExecutionProvider"])
        self._wake = ort.InferenceSession(p(f"{wake_model}.onnx"), providers=["CPUExecutionProvider"])
        self._mi = self._mel.get_inputs()[0].name
        self._ei = self._emb.get_inputs()[0].name
        self._wi = self._wake.get_inputs()[0].name

    def melspec(self, audio: np.ndarray) -> np.ndarray:
        o = self._mel.run(None, {self._mi: audio[None, :].astype(np.float32)})[0]
        m = np.squeeze(o, axis=(0, 1))  # [F, 32]
        return m / 10.0 + 2.0

    def embeddings(self, mel: np.ndarray) -> np.ndarray:
        out = []
        i = 0
        while i + MEL_WINDOW <= mel.shape[0]:
            w = mel[i : i + MEL_WINDOW][None, :, :, None].astype(np.float32)
            out.append(np.squeeze(self._emb.run(None, {self._ei: w})[0]))
            i += MEL_STEP
        return np.array(out, dtype=np.float32)

    def score(self, audio: np.ndarray) -> float | None:
        emb = self.embeddings(self.melspec(audio))
        if len(emb) < WAKE_WINDOW:
            return None
        w = emb[-WAKE_WINDOW:][None, :, :].astype(np.float32)
        return float(np.squeeze(self._wake.run(None, {self._wi: w})[0]))


def _load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as wf:
        assert wf.getframerate() == 16000, "expects 16 kHz wav"
        frames = wf.readframes(wf.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32)


if __name__ == "__main__":
    ref = WakeReference()
    if len(sys.argv) > 1:
        audio = _load_wav(sys.argv[1])
        print(f"{sys.argv[1]}: score={ref.score(audio)}")
    else:
        # Deterministic non-wake inputs — the Rust port must match these within
        # tolerance (tract vs onnxruntime). All should score near 0.
        cases = {
            "zeros": np.zeros(32000),
            "sine440": 10000 * np.sin(2 * np.pi * 440 * np.arange(32000) / 16000),
            "prng": np.random.RandomState(42).randint(-8000, 8000, 32000).astype(float),
        }
        for name, a in cases.items():
            print(f"{name:8} score={ref.score(a):.6f}")
