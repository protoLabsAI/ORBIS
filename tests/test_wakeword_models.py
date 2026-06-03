"""Wake-word model catalog + status/delete roundtrip (no network)."""

from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.fixture()
def wm(tmp_path, monkeypatch):
    """Reimport the module with ORBIS_MODELS_DIR pointed at a tmp dir so the
    on-disk status checks are isolated."""
    monkeypatch.setenv("ORBIS_MODELS_DIR", str(tmp_path))
    import voice.wakeword_models as mod

    importlib.reload(mod)
    return mod


def test_models_dir_honors_env(wm, tmp_path):
    assert wm.models_dir() == tmp_path / "wakeword"
    assert wm.models_dir().is_dir()  # created on resolve


def test_catalog_shape_and_defaults(wm):
    cat = wm.catalog()
    ids = {m["id"] for m in cat}
    # The recommended custom model + the two shared deps must be present.
    assert {"hey_orbis", "melspectrogram", "embedding"} <= ids
    by_id = {m["id"]: m for m in cat}
    assert by_id["hey_orbis"]["recommended"] is True
    assert by_id["hey_orbis"]["kind"] == "wake"
    assert by_id["melspectrogram"]["kind"] == "shared"
    # Exactly one recommended default.
    assert sum(1 for m in cat if m["recommended"]) == 1
    # Nothing is downloaded into a fresh dir.
    assert all(m["downloaded"] is False for m in cat)


def test_every_model_has_required_fields(wm):
    for m in wm.catalog():
        for key in ("id", "name", "filename", "url", "size_kb", "kind"):
            assert m[key], f"{m['id']} missing {key}"
        assert m["filename"].endswith(".onnx")
        assert m["url"].startswith("https://")
        assert m["size_kb"] > 0
        assert m["kind"] in ("shared", "wake")


def test_status_and_delete_roundtrip(wm):
    m = wm.get("hey_orbis")
    assert m is not None
    assert wm.is_downloaded(m) is False
    # Simulate a completed download.
    (wm.models_dir() / m.filename).write_bytes(b"\x00onnx")
    assert wm.is_downloaded(m) is True
    assert any(c["downloaded"] for c in wm.catalog() if c["id"] == "hey_orbis")
    assert wm.delete("hey_orbis") is True
    assert wm.is_downloaded(m) is False
    # Deleting a model that isn't present (or unknown) is a no-op False.
    assert wm.delete("hey_orbis") is False
    assert wm.delete("does_not_exist") is False


def test_get_unknown_returns_none(wm):
    assert wm.get("nope") is None


def test_download_writes_file_and_reports_progress(wm, respx_mock):
    payload = b"ONNX-bytes" * 200  # ~2 KB, multiple 64 KB chunks not needed
    respx_mock.get(wm.get("hey_orbis").url).respond(
        content=payload, headers={"content-length": str(len(payload))}
    )
    seen: list[tuple[int, int]] = []
    path = asyncio.run(
        wm.download("hey_orbis", on_progress=lambda d, t: seen.append((d, t)))
    )
    assert path.name == "hey_orbis.onnx"
    assert path.read_bytes() == payload
    assert seen, "progress callback never fired"
    assert seen[-1] == (len(payload), len(payload))
    # Atomic install leaves no .partial behind.
    assert not path.with_suffix(path.suffix + ".partial").exists()


def test_download_skips_when_present(wm, respx_mock):
    dest = wm.models_dir() / wm.get("hey_orbis").filename
    dest.write_bytes(b"already-here")
    path = asyncio.run(wm.download("hey_orbis"))
    assert path == dest
    assert path.read_bytes() == b"already-here"
    assert not respx_mock.calls, "must not hit the network when already present"


def test_download_unknown_model_raises(wm):
    with pytest.raises(ValueError):
        asyncio.run(wm.download("does_not_exist"))
