"""Wake-word model catalog + downloads (orbis wake-word).

A small manifest of openWakeWord-format ONNX models the user can pick + download
from the UI, plus a resumable downloader. The Rust wake-word detector (Phase: see
docs/internal/wake-word.md) loads the chosen wake model + the two shared models
(melspectrogram + embedding) from the same on-disk dir.

Patterned on Handy (cjpais/Handy)'s model manager: a per-model manifest with
size + source url + recommended flag, downloads to the app-data `models/`
directory, status recomputed from disk.

openWakeWord runtime is three ONNX models in sequence: melspectrogram →
embedding (both SHARED across all wake words) → the per-phrase wake model. So the
two shared models are a base dependency every wake word needs; each wake word is
just its own small classifier.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Stock openWakeWord release (dscripka/openWakeWord) — the shared models + the
# common pre-trained wake words. Pinned to a release tag for reproducibility.
_OWW = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
# The user's custom "Hey Orbis" model on Hugging Face.
_HEY_ORBIS = "https://huggingface.co/protoLabsAI/hey-orbis-wakeword/resolve/main/hey_orbis.onnx"


@dataclass
class WakeModel:
    id: str
    name: str
    description: str
    filename: str
    url: str
    size_kb: int
    #: "shared" = base dependency (melspec/embedding) every wake word needs;
    #: "wake" = a per-phrase classifier the user can enable.
    kind: str
    recommended: bool = False


# The catalog. `hey_orbis` is the default/recommended; the stock oWW set gives
# the user familiar phrases to try. The two `shared` entries are pulled in
# automatically as dependencies of any wake word.
_CATALOG: list[WakeModel] = [
    WakeModel(
        "melspectrogram", "Mel spectrogram", "Shared audio front-end (required).",
        "melspectrogram.onnx", f"{_OWW}/melspectrogram.onnx", 1063, "shared",
    ),
    WakeModel(
        "embedding", "Speech embedding", "Shared Google speech embedding (required).",
        "embedding_model.onnx", f"{_OWW}/embedding_model.onnx", 1295, "shared",
    ),
    WakeModel(
        "hey_orbis", "Hey Orbis", "ORBIS's own wake word (94% recall). Recommended.",
        "hey_orbis.onnx", _HEY_ORBIS, 198, "wake", recommended=True,
    ),
    WakeModel(
        "alexa", "Alexa", "Stock openWakeWord phrase.",
        "alexa_v0.1.onnx", f"{_OWW}/alexa_v0.1.onnx", 834, "wake",
    ),
    WakeModel(
        "hey_jarvis", "Hey Jarvis", "Stock openWakeWord phrase.",
        "hey_jarvis_v0.1.onnx", f"{_OWW}/hey_jarvis_v0.1.onnx", 1242, "wake",
    ),
    WakeModel(
        "hey_mycroft", "Hey Mycroft", "Stock openWakeWord phrase.",
        "hey_mycroft_v0.1.onnx", f"{_OWW}/hey_mycroft_v0.1.onnx", 838, "wake",
    ),
    WakeModel(
        "hey_rhasspy", "Hey Rhasspy", "Stock openWakeWord phrase.",
        "hey_rhasspy_v0.1.onnx", f"{_OWW}/hey_rhasspy_v0.1.onnx", 199, "wake",
    ),
    WakeModel(
        "timer", "Timer", "Stock openWakeWord phrase (\"set a timer\").",
        "timer_v0.1.onnx", f"{_OWW}/timer_v0.1.onnx", 1702, "wake",
    ),
    WakeModel(
        "weather", "Weather", "Stock openWakeWord phrase (\"what's the weather\").",
        "weather_v0.1.onnx", f"{_OWW}/weather_v0.1.onnx", 1122, "wake",
    ),
]

_BY_ID = {m.id: m for m in _CATALOG}


def models_dir() -> Path:
    """Where wake-word ONNX models live. Shared with the Rust detector via
    ORBIS_MODELS_DIR (set by the Tauri shell to the app-data `models/` dir);
    falls back to a repo-local dir for dev."""
    base = os.environ.get("ORBIS_MODELS_DIR") or "models"
    d = Path(base).expanduser() / "wakeword"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_downloaded(m: WakeModel) -> bool:
    p = models_dir() / m.filename
    return p.exists() and p.stat().st_size > 0


def catalog() -> list[dict]:
    """The catalog with live download status, for the picker UI."""
    out = []
    for m in _CATALOG:
        d = asdict(m)
        d["downloaded"] = is_downloaded(m)
        out.append(d)
    return out


def get(model_id: str) -> WakeModel | None:
    return _BY_ID.get(model_id)


async def iter_download(model_id: str) -> AsyncGenerator[dict, None]:
    """Download one model, yielding ``{"downloaded", "total"}`` (bytes) as data
    arrives, and performing the atomic install on completion. Throttling is the
    caller's job. The streaming download endpoint drives this directly.

    Resumable: keeps ``<file>.partial`` and sends a Range header; if the server
    ignores it (200 not 206) we restart clean. Atomic install via rename."""
    m = _BY_ID.get(model_id)
    if m is None:
        raise ValueError(f"unknown wake model {model_id!r}")
    dest = models_dir() / m.filename
    if dest.exists() and dest.stat().st_size > 0:
        sz = dest.stat().st_size
        yield {"downloaded": sz, "total": sz}
        return
    partial = dest.with_suffix(dest.suffix + ".partial")
    have = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
        async with client.stream("GET", m.url, headers=headers) as r:
            if r.status_code not in (200, 206):
                raise RuntimeError(f"{model_id}: HTTP {r.status_code} from {m.url}")
            # Server ignored our Range — start fresh so we don't corrupt.
            if have and r.status_code == 200:
                have = 0
            total = have + int(r.headers.get("content-length") or 0)
            mode = "ab" if have else "wb"
            with open(partial, mode) as f:
                downloaded = have
                async for chunk in r.aiter_bytes(64 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    yield {"downloaded": downloaded, "total": total}
    partial.rename(dest)
    logger.info(f"[wakeword] downloaded {m.filename} → {dest}")


async def download(model_id: str, on_progress=None) -> Path:
    """Convenience wrapper around :func:`iter_download` — exhausts the stream,
    invoking ``on_progress(downloaded, total)`` per tick, and returns the path."""
    async for p in iter_download(model_id):
        if on_progress is not None:
            on_progress(p["downloaded"], p["total"])
    return models_dir() / _BY_ID[model_id].filename


def delete(model_id: str) -> bool:
    m = _BY_ID.get(model_id)
    if m is None:
        return False
    p = models_dir() / m.filename
    if p.exists():
        p.unlink()
        return True
    return False
