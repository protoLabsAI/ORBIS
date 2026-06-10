"""Tests for the `.orbis` orb-definition store + /api/orbs endpoints.

The validator here mirrors packages/orb-runtime/src/definition/validate.ts
rule for rule — these tests pin the Python half of that contract:
  validator: happy path + the rejection classes that matter
  store:     save/list/delete roundtrip, atomicity posture, id traversal
  API:       GET list, POST import (gated, 400 invalid), DELETE (404)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth import require_user
from auth.users import User

from agent.orb_definitions import (
    OrbDefinitionError,
    delete_definition,
    list_definitions,
    save_definition,
    validate_definition,
)


def make_definition(**overrides) -> dict:
    """A minimal valid definition; override fields per test."""
    d = {
        "format": "orbis-orb",
        "version": 1,
        "id": "test-orb",
        "name": "Test Orb",
        "engine": "raymarch-v1",
        "shaders": {"fragment": "void main() { gl_FragColor = vec4(1.0); }"},
        "uniforms": {
            "uGlow": {"type": "float", "default": 1.0},
            "uPhases": {"type": "vec4", "default": [1, 2, 3, 4]},
        },
        "fields": [
            {"kind": "slider", "key": "glow", "label": "Glow",
             "section": "energy", "min": 0.0, "max": 2.0, "step": 0.05},
            {"kind": "color", "key": "primaryEnergy", "label": "Primary",
             "section": "color"},
        ],
        "palettes": {"Default": {"glow": 1.0, "primaryEnergy": "#9b87f2"}},
        "defaultPalette": "Default",
        "bindings": [
            {"target": "uGlow", "signal": "param.glow"},
            {"target": "uGlow", "signal": "bot.level", "op": "add", "scale": 0.5},
            {"target": "uPhases.x", "signal": "time"},
            {"target": "uPrimaryColor", "signal": "snap.primary"},
        ],
    }
    d.update(overrides)
    return d


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def test_valid_definition_passes():
    assert validate_definition(make_definition()) == []


@pytest.mark.parametrize(
    "mutation, fragment",
    [
        ({"format": "nope"}, "format"),
        ({"version": 2}, "version"),
        ({"id": "Bad Id!"}, "id"),
        ({"name": ""}, "name"),
        ({"engine": "particles-v1"}, "engine"),
        ({"shaders": {"fragment": ""}}, "fragment"),
        ({"palettes": {}}, "palettes"),
        ({"defaultPalette": "Missing"}, "defaultPalette"),
    ],
)
def test_rejections(mutation, fragment):
    errors = validate_definition(make_definition(**mutation))
    assert errors, f"expected rejection for {mutation}"
    assert any(fragment in e for e in errors)


def test_rejects_binding_to_unknown_uniform():
    d = make_definition(bindings=[{"target": "uNope", "signal": "time"}])
    assert any("uNope" in e for e in validate_definition(d))


def test_rejects_binding_to_reserved_target():
    d = make_definition(bindings=[{"target": "uTime", "signal": "time"}])
    assert any("engine-managed" in e for e in validate_definition(d))


def test_rejects_unknown_signal():
    d = make_definition(bindings=[{"target": "uGlow", "signal": "fft.bass"}])
    assert any("unknown" in e for e in validate_definition(d))


def test_rejects_color_target_with_scalar_signal():
    d = make_definition(bindings=[{"target": "uPrimaryColor", "signal": "time"}])
    assert any("color signal" in e for e in validate_definition(d))


def test_rejects_uniform_shadowing_standard():
    d = make_definition(uniforms={"uTime": {"type": "float"}})
    assert any("shadows" in e for e in validate_definition(d))


def test_rejects_component_out_of_range():
    d = make_definition(bindings=[{"target": "uPhases.w", "signal": "time"}])
    assert validate_definition(d) == []  # w valid on vec4
    d2 = make_definition(
        uniforms={"uVec": {"type": "vec2"}},
        bindings=[{"target": "uVec.z", "signal": "time"}],
    )
    assert any("out of range" in e for e in validate_definition(d2))


def test_rejects_oversized_fragment():
    d = make_definition(shaders={"fragment": "x" * 300_000})
    assert any("exceeds" in e for e in validate_definition(d))


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@pytest.fixture
def orbs_dir_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "orbs"
    monkeypatch.setenv("ORBIS_ORBS_DIR", str(d))
    return d


def test_save_list_delete_roundtrip(orbs_dir_env: Path):
    dest, replaced = save_definition(make_definition())
    assert dest == orbs_dir_env / "test-orb.orbis"
    assert replaced is False

    listed = list_definitions()
    assert [d["id"] for d in listed] == ["test-orb"]

    # Same id replaces.
    _, replaced = save_definition(make_definition(name="Test Orb v2"))
    assert replaced is True
    assert list_definitions()[0]["name"] == "Test Orb v2"

    assert delete_definition("test-orb") is True
    assert list_definitions() == []
    assert delete_definition("test-orb") is False


def test_save_rejects_invalid(orbs_dir_env: Path):
    with pytest.raises(OrbDefinitionError) as ei:
        save_definition(make_definition(engine="nope"))
    assert ei.value.errors
    assert list_definitions() == []


def test_list_skips_corrupt_files(orbs_dir_env: Path):
    save_definition(make_definition())
    orbs_dir_env.joinpath("broken.orbis").write_text("{not json")
    orbs_dir_env.joinpath("invalid.orbis").write_text(json.dumps({"format": "nope"}))
    assert [d["id"] for d in list_definitions()] == ["test-orb"]


def test_delete_refuses_path_traversal(orbs_dir_env: Path, tmp_path: Path):
    outside = tmp_path / "victim.orbis"
    outside.write_text("{}")
    assert delete_definition("../victim") is False
    assert outside.exists()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _make_user() -> User:
    return User(id="owner", display_name="Owner", api_key_hash="hash")


@pytest.fixture
def client(orbs_dir_env: Path) -> TestClient:
    c = TestClient(app_module.app)
    app_module.app.dependency_overrides[require_user] = _make_user
    yield c
    app_module.app.dependency_overrides.clear()


def test_api_list_empty(client: TestClient):
    r = client.get("/api/orbs")
    assert r.status_code == 200
    assert r.json() == {"orbs": []}


def test_api_import_roundtrip(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import agent.entitlement
    monkeypatch.setattr(agent.entitlement, "has_customization", lambda _mem: True)

    r = client.post("/api/orbs", json=make_definition())
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "test-orb", "replaced": False}

    r = client.get("/api/orbs")
    assert [d["id"] for d in r.json()["orbs"]] == ["test-orb"]

    r = client.post("/api/orbs", json=make_definition())
    assert r.json()["replaced"] is True

    r = client.delete("/api/orbs/test-orb")
    assert r.status_code == 200
    assert client.get("/api/orbs").json() == {"orbs": []}


def test_api_import_403_when_not_entitled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    import agent.entitlement
    monkeypatch.setattr(agent.entitlement, "has_customization", lambda _mem: False)
    r = client.post("/api/orbs", json=make_definition())
    assert r.status_code == 403
    assert "unlock" in r.json()["detail"].lower()


def test_api_import_400_on_invalid(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import agent.entitlement
    monkeypatch.setattr(agent.entitlement, "has_customization", lambda _mem: True)
    r = client.post("/api/orbs", json=make_definition(engine="nope"))
    assert r.status_code == 400
    assert r.json()["errors"]


def test_api_delete_404_on_missing(client: TestClient):
    r = client.delete("/api/orbs/ghost")
    assert r.status_code == 404
