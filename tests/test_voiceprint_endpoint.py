"""Tests for the /api/voiceprint enrollment endpoints (#35 PR 1.3).

Covers status / enroll / delete flows. Speechbrain is not a test dep —
we patch ECAPAEmbedder so the encode path runs deterministically and
we can assert the saved voiceprint shape without downloading a model.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

import app as app_module
from auth import require_user
from auth.users import User


def _make_user() -> User:
    return User(id="owner", display_name="Owner", api_key_hash="hash")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SPEAKER_GATE_VOICEPRINT_PATH", str(tmp_path / "vp.npy"))
    # Repath data dir too in case any side effect hits get_db_path etc.
    monkeypatch.setenv("ORBIS_CONFIG", str(tmp_path / "orbis.yaml"))

    c = TestClient(app_module.app)
    app_module.app.dependency_overrides[require_user] = _make_user
    yield c
    app_module.app.dependency_overrides.clear()


def _make_wav(duration_secs: float, sample_rate: int = 16000) -> bytes:
    """Build a real WAV blob — silence at the requested duration."""
    n = int(duration_secs * sample_rate)
    samples = np.zeros(n, dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


# --- /api/voiceprint/status ----------------------------------------------


def test_status_reports_unenrolled_when_no_file(client: TestClient) -> None:
    r = client.get("/api/voiceprint/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enrolled"] is False
    assert "path" in body


def test_status_reports_enrolled_when_file_exists(
    client: TestClient, tmp_path: Path,
) -> None:
    p = tmp_path / "vp.npy"
    np.save(p, np.zeros(192, dtype=np.float32))
    r = client.get("/api/voiceprint/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enrolled"] is True


def test_status_reports_embedder_availability(client: TestClient) -> None:
    """The wizard branches its UX on whether speechbrain is installed.
    Test environment doesn't ship it, so False is expected here."""
    r = client.get("/api/voiceprint/status")
    body = r.json()
    # Whatever it is, must be a bool.
    assert isinstance(body["embedder_available"], bool)


# --- /api/voiceprint/enroll ----------------------------------------------


class _StubEmbedder:
    """In-place ECAPAEmbedder. Returns a stable 192-dim vector."""

    def encode(self, wav: np.ndarray, sample_rate: int) -> np.ndarray:
        # Deterministic vector that depends on the wav's mean so two
        # different recordings produce different embeddings.
        seed = int(abs(float(wav.mean())) * 1e6) & 0xFFFFFFFF
        rng = np.random.RandomState(seed)
        return rng.randn(192).astype(np.float32)


def test_enroll_saves_voiceprint_to_path(
    client: TestClient, tmp_path: Path,
) -> None:
    wav_bytes = _make_wav(duration_secs=5.0)
    with patch("app._is_speechbrain_available", return_value=True):
        with patch("agent.ecapa_embedder.ECAPAEmbedder", _StubEmbedder):
            r = client.post(
                "/api/voiceprint/enroll",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enrolled"] is True
    assert body["embedding_dim"] == 192
    assert body["sample_rate"] == 16000
    # File written.
    p = tmp_path / "vp.npy"
    assert p.exists()
    saved = np.load(p)
    assert saved.shape == (192,)


def test_enroll_rejects_empty_body(client: TestClient) -> None:
    with patch("app._is_speechbrain_available", return_value=True):
        r = client.post(
            "/api/voiceprint/enroll",
            content=b"",
            headers={"Content-Type": "audio/wav"},
        )
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()


def test_enroll_rejects_undecodable_audio(client: TestClient) -> None:
    with patch("app._is_speechbrain_available", return_value=True):
        r = client.post(
            "/api/voiceprint/enroll",
            content=b"this is not a wav file",
            headers={"Content-Type": "audio/wav"},
        )
    assert r.status_code == 400
    assert "decode" in r.json()["detail"].lower()


def test_enroll_rejects_too_short_recording(client: TestClient) -> None:
    """1 second of audio isn't enough for a stable embedding."""
    short = _make_wav(duration_secs=1.0)
    with patch("app._is_speechbrain_available", return_value=True):
        with patch("agent.ecapa_embedder.ECAPAEmbedder", _StubEmbedder):
            r = client.post(
                "/api/voiceprint/enroll",
                content=short,
                headers={"Content-Type": "audio/wav"},
            )
    assert r.status_code == 400
    assert "at least" in r.json()["detail"].lower()


def test_enroll_truncates_overly_long_recording(
    client: TestClient, tmp_path: Path,
) -> None:
    """31s recording shouldn't 400 — silently truncate to the cap."""
    long = _make_wav(duration_secs=45.0)
    with patch("app._is_speechbrain_available", return_value=True):
        with patch("agent.ecapa_embedder.ECAPAEmbedder", _StubEmbedder):
            r = client.post(
                "/api/voiceprint/enroll",
                content=long,
                headers={"Content-Type": "audio/wav"},
            )
    assert r.status_code == 200, r.text
    assert r.json()["duration_secs"] <= 30.0


def test_enroll_returns_501_when_speechbrain_missing(client: TestClient) -> None:
    """When the [speaker-id] extra isn't installed, the endpoint must
    refuse with an actionable hint rather than crash."""
    with patch("app._is_speechbrain_available", return_value=False):
        r = client.post(
            "/api/voiceprint/enroll",
            content=_make_wav(5.0),
            headers={"Content-Type": "audio/wav"},
        )
    assert r.status_code == 501
    assert "speaker-id" in r.json()["detail"]


def test_enroll_handles_stereo_audio_by_downmixing(
    client: TestClient, tmp_path: Path,
) -> None:
    """Browsers may capture stereo even when num_channels=1 was requested.
    Downmix to mono rather than rejecting."""
    sample_rate = 16000
    n = sample_rate * 5
    stereo = np.zeros((n, 2), dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, stereo, sample_rate, format="WAV", subtype="PCM_16")
    with patch("app._is_speechbrain_available", return_value=True):
        with patch("agent.ecapa_embedder.ECAPAEmbedder", _StubEmbedder):
            r = client.post(
                "/api/voiceprint/enroll",
                content=buf.getvalue(),
                headers={"Content-Type": "audio/wav"},
            )
    assert r.status_code == 200, r.text


# --- DELETE /api/voiceprint ----------------------------------------------


def test_delete_removes_voiceprint(client: TestClient, tmp_path: Path) -> None:
    p = tmp_path / "vp.npy"
    np.save(p, np.zeros(192, dtype=np.float32))
    assert p.exists()

    r = client.delete("/api/voiceprint")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert not p.exists()


def test_delete_is_idempotent_when_no_voiceprint(
    client: TestClient,
) -> None:
    """Deleting when nothing's there should not 404 — the desired
    end state (no voiceprint) is achieved."""
    r = client.delete("/api/voiceprint")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
