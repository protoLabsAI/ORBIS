"""Wake-word model catalog + status/delete roundtrip (no network)."""

from __future__ import annotations

import asyncio
import importlib
import hashlib

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
    assert by_id["hey_orbis"]["recommended"] is False
    assert by_id["hey_jarvis"]["recommended"] is True
    assert "experimental" in by_id["hey_orbis"]["description"].lower()
    assert "94%" not in by_id["hey_orbis"]["description"]
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
    payload = b"\x00onnx"
    m.sha256 = hashlib.sha256(payload).hexdigest()
    (wm.models_dir() / m.filename).write_bytes(payload)
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
    wm.get("hey_orbis").sha256 = hashlib.sha256(payload).hexdigest()
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
    wm.get("hey_orbis").sha256 = hashlib.sha256(b"already-here").hexdigest()
    path = asyncio.run(wm.download("hey_orbis"))
    assert path == dest
    assert path.read_bytes() == b"already-here"
    assert not respx_mock.calls, "must not hit the network when already present"


def test_download_unknown_model_raises(wm):
    with pytest.raises(ValueError):
        asyncio.run(wm.download("does_not_exist"))


def test_corrupt_existing_file_is_not_installed(wm):
    m = wm.get("hey_orbis")
    (wm.models_dir() / m.filename).write_bytes(b"corrupt")
    assert not wm.is_downloaded(m)


def test_checksum_failure_never_installs_and_retry_starts_clean(wm, respx_mock):
    m = wm.get("hey_orbis")
    route = respx_mock.get(m.url).respond(content=b"wrong-model")
    with pytest.raises(RuntimeError, match="verification failed"):
        asyncio.run(wm.download(m.id))
    assert not wm.is_downloaded(m)
    assert not (wm.models_dir() / (m.filename + ".partial")).exists()
    payload = b"correct-model"
    m.sha256 = hashlib.sha256(payload).hexdigest()
    route.respond(content=payload)
    assert asyncio.run(wm.download(m.id)).read_bytes() == payload


@pytest.mark.parametrize("supports_range", [True, False])
def test_resumed_download_or_range_ignored_verifies_before_install(wm, respx_mock, supports_range):
    m = wm.get("hey_jarvis")
    payload = b"verified-model"
    m.sha256 = hashlib.sha256(payload).hexdigest()
    (wm.models_dir() / (m.filename + ".partial")).write_bytes(payload[:4])
    route = respx_mock.get(m.url).respond(
        status_code=206 if supports_range else 200,
        content=payload[4:] if supports_range else payload,
    )
    assert asyncio.run(wm.download(m.id)).read_bytes() == payload
    assert route.calls[0].request.headers["range"] == "bytes=4-"


def test_http_error_is_actionable_and_not_installed(wm, respx_mock):
    m = wm.get("hey_orbis")
    respx_mock.get(m.url).respond(status_code=503)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        asyncio.run(wm.download(m.id))
    assert not wm.is_downloaded(m)


def test_concurrent_downloads_share_one_atomic_writer(wm, respx_mock):
    m = wm.get("hey_jarvis")
    payload = b"verified-model"
    m.sha256 = hashlib.sha256(payload).hexdigest()
    route = respx_mock.get(m.url).respond(content=payload)

    async def downloads():
        return await asyncio.gather(wm.download(m.id), wm.download(m.id))

    first, second = asyncio.run(downloads())
    assert first == second
    assert first.read_bytes() == payload
    assert len(route.calls) == 1


def test_pinned_catalog_shapes_and_hashes(wm):
    assert wm.get("timer").embedding_frames == 34
    assert wm.get("weather").embedding_frames == 22
    assert wm.get("hey_jarvis").embedding_frames == 16
    assert "/main/" not in wm.get("hey_orbis").url
    assert all(len(m["sha256"]) == 64 for m in wm.catalog())


def test_rejected_resume_clears_partial_for_retry(wm, respx_mock):
    m = wm.get("hey_jarvis")
    payload = b"verified-model"
    m.sha256 = hashlib.sha256(payload).hexdigest()
    partial = wm.models_dir() / (m.filename + ".partial")
    partial.write_bytes(b"obsolete-partial")
    route = respx_mock.get(m.url).respond(status_code=416)
    with pytest.raises(RuntimeError, match="needs a retry"):
        asyncio.run(wm.download(m.id))
    assert not partial.exists()
    route.respond(content=payload)
    assert asyncio.run(wm.download(m.id)).read_bytes() == payload
    assert "range" not in route.calls[-1].request.headers
