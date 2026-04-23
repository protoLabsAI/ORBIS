"""Tests for the /api/config POST entitlement gate.

Direct /api/config POSTs would otherwise bypass the tool-call path's
paid-tier gate. This endpoint rejects ``orb`` block edits when the
caller lacks the customization entitlement, while still accepting
persona + voice edits (which are free-tier features).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth.users import User
from auth import require_user


def _make_user() -> User:
    return User(id="owner", display_name="Owner", api_key_hash="hash")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Isolate config writes to a tempfile so the test can't corrupt
    # the repo's checked-in config.
    cfg = tmp_path / "orbis.yaml"
    monkeypatch.setenv("ORBIS_CONFIG", str(cfg))

    c = TestClient(app_module.app)
    app_module.app.dependency_overrides[require_user] = _make_user
    yield c
    app_module.app.dependency_overrides.clear()


# --- gate: orb block requires entitlement ----------------------------------


def test_orb_patch_403s_when_not_entitled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    # Force the gate closed regardless of Stripe config state.
    import agent.entitlement
    monkeypatch.setattr(agent.entitlement, "has_customization", lambda _mem: False)
    r = client.post("/api/config", json={"orb": {"variant": "nebula"}})
    assert r.status_code == 403
    assert "paid" in r.json()["detail"].lower() or "unlock" in r.json()["detail"].lower()


def test_orb_patch_200s_when_entitled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    import agent.entitlement
    monkeypatch.setattr(agent.entitlement, "has_customization", lambda _mem: True)
    r = client.post("/api/config", json={
        "orb": {"variant": "nebula", "palette": "Andromeda"},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["config"]["orb"]["variant"] == "nebula"


def test_persona_patch_allowed_without_entitlement(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    """Persona changes are always free-tier — no entitlement required."""
    import agent.entitlement
    monkeypatch.setattr(agent.entitlement, "has_customization", lambda _mem: False)
    r = client.post("/api/config", json={"persona": {"name": "Atlas"}})
    assert r.status_code == 200
    assert r.json()["config"]["persona"]["name"] == "Atlas"


def test_voice_patch_allowed_without_entitlement(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    """TTS provider swaps are always free-tier — no entitlement required."""
    import agent.entitlement
    monkeypatch.setattr(agent.entitlement, "has_customization", lambda _mem: False)
    r = client.post("/api/config", json={"voice": {"tts_backend": "kokoro"}})
    assert r.status_code == 200
    assert r.json()["config"]["voice"]["tts_backend"] == "kokoro"


def test_mixed_patch_with_orb_403s_when_not_entitled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    """A request mixing free (persona) + paid (orb) blocks rejects the
    whole thing when not entitled, rather than partially applying."""
    import agent.entitlement
    monkeypatch.setattr(agent.entitlement, "has_customization", lambda _mem: False)
    r = client.post("/api/config", json={
        "persona": {"name": "Atlas"},
        "orb": {"variant": "nebula"},
    })
    assert r.status_code == 403
