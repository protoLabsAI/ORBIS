"""Tests for /api/delegates CRUD + /api/delegates/test reachability probe.

Spans the full lifecycle a UI client would exercise:
  GET → list (empty + populated)
  POST → 201 happy path, 409 duplicate, 400 schema rejection
  PUT → update, 404 missing, 400 path/body name mismatch
  DELETE → 204 happy, 404 missing
  POST /test → a2a happy, a2a auth-reject, openai happy, schema reject

Atomic-write semantics covered separately at the config-store layer
(tests/test_delegate_config_store.py — sister suite).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import agent.delegate_config_store as store
import app as app_module
from app import app


@pytest.fixture
def isolated_delegates_yaml(tmp_path, monkeypatch):
    """Point the config store + the runtime registry at a per-test
    delegates.yaml so we don't stomp the dev config."""
    p = tmp_path / "delegates.yaml"
    monkeypatch.setattr(store, "DEFAULT_PATH", str(p))
    # The API endpoints reload the runtime registry against the path
    # configured at app boot — swap it for the test's path so reload
    # actually reads our fixture.
    from agent.delegates import DelegateRegistry
    test_registry = DelegateRegistry(p)
    monkeypatch.setattr(app_module, "_DELEGATES", test_registry)
    return p


@pytest.fixture
def client(isolated_delegates_yaml):
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


def test_get_empty_returns_empty_list(client, isolated_delegates_yaml):
    r = client.get("/api/delegates")
    assert r.status_code == 200
    assert r.json() == {"delegates": []}


def test_get_returns_existing_entries_with_configured_flag(
    client, isolated_delegates_yaml,
):
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: Chief of staff.\n"
        "    url: http://ava:3008/a2a\n"
    )
    # Reload the runtime registry so `configured` reflects the new file.
    app_module._DELEGATES.reload()
    r = client.get("/api/delegates")
    assert r.status_code == 200
    body = r.json()
    assert len(body["delegates"]) == 1
    entry = body["delegates"][0]
    assert entry["name"] == "ava"
    assert entry["type"] == "a2a"
    assert entry["configured"] is True


def test_get_flags_unconfigured_when_runtime_parse_fails(
    client, isolated_delegates_yaml,
):
    """Entry on disk that the runtime registry rejects (e.g. missing
    model on an openai delegate) shows up as configured=False so the
    UI can flag it inline."""
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: broken\n"
        "    type: openai\n"
        "    description: needs a model.\n"
        "    url: https://example.com/v1\n"
        # model: ← missing on purpose
    )
    app_module._DELEGATES.reload()
    body = client.get("/api/delegates").json()
    assert body["delegates"][0]["configured"] is False


# ---------------------------------------------------------------------------
# POST (create)
# ---------------------------------------------------------------------------


def test_post_creates_and_persists(client, isolated_delegates_yaml):
    payload = {
        "name": "ava",
        "type": "a2a",
        "description": "Chief of staff.",
        "url": "http://ava:3008/a2a",
    }
    r = client.post("/api/delegates", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    # Persisted to disk
    assert "name: ava" in isolated_delegates_yaml.read_text()
    # Runtime registry reloaded — name now visible
    assert "ava" in app_module._DELEGATES.names()


def test_post_duplicate_name_returns_409(client, isolated_delegates_yaml):
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: hi.\n"
        "    url: http://ava/a2a\n"
    )
    payload = {
        "name": "ava",
        "type": "a2a",
        "description": "second one.",
        "url": "http://ava2/a2a",
    }
    r = client.post("/api/delegates", json=payload)
    assert r.status_code == 409
    assert "already exists" in r.json()["error"]


def test_post_schema_rejection_returns_400(client, isolated_delegates_yaml):
    payload = {
        "name": "incomplete",
        "type": "openai",
        "description": "missing model.",
        "url": "https://example.com/v1",
        # model: ← missing
    }
    r = client.post("/api/delegates", json=payload)
    assert r.status_code == 400
    assert "model" in r.json()["error"]


# ---------------------------------------------------------------------------
# PUT (update)
# ---------------------------------------------------------------------------


def test_put_updates_existing(client, isolated_delegates_yaml):
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: old.\n"
        "    url: http://ava/a2a\n"
    )
    r = client.put(
        "/api/delegates/ava",
        json={
            "name": "ava",
            "type": "a2a",
            "description": "new description.",
            "url": "http://ava-2/a2a",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["delegates"][0]["url"] == "http://ava-2/a2a"
    assert body["delegates"][0]["description"] == "new description."


def test_put_missing_returns_404(client, isolated_delegates_yaml):
    r = client.put(
        "/api/delegates/ghost",
        json={
            "name": "ghost",
            "type": "a2a",
            "description": "spooky.",
            "url": "http://nowhere/a2a",
        },
    )
    assert r.status_code == 404


def test_put_path_body_name_mismatch_returns_400(client, isolated_delegates_yaml):
    """Renames must use DELETE+POST so the registry sees both lifecycle
    events. PUT mismatching name → 400 with a hint."""
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: hi.\n"
        "    url: http://ava/a2a\n"
    )
    r = client.put(
        "/api/delegates/ava",
        json={
            "name": "renamed",
            "type": "a2a",
            "description": "hi.",
            "url": "http://ava/a2a",
        },
    )
    assert r.status_code == 400
    assert "rename" in r.json()["error"]


def test_put_omits_name_in_body_inherits_from_path(client, isolated_delegates_yaml):
    """Body without a `name` field is filled in from the path — saves
    the UI from having to duplicate it."""
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: old.\n"
        "    url: http://ava/a2a\n"
    )
    r = client.put(
        "/api/delegates/ava",
        json={
            "type": "a2a",
            "description": "new from path.",
            "url": "http://ava/a2a",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["delegates"][0]["description"] == "new from path."


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_removes_entry(client, isolated_delegates_yaml):
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: hi.\n"
        "    url: http://ava/a2a\n"
    )
    r = client.delete("/api/delegates/ava")
    assert r.status_code == 200
    assert r.json()["delegates"] == []
    assert "ava" not in app_module._DELEGATES.names()


def test_delete_missing_returns_404(client, isolated_delegates_yaml):
    r = client.delete("/api/delegates/ghost")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /test (reachability probe)
# ---------------------------------------------------------------------------


def test_test_a2a_happy_path(client, isolated_delegates_yaml, respx_mock):
    respx_mock.get("http://ava:3008/.well-known/agent-card.json").respond(
        status_code=200,
        json={"name": "ava", "version": "1.0.0"},
    )
    r = client.post(
        "/api/delegates/test",
        json={
            "name": "ava",
            "type": "a2a",
            "description": "ping.",
            "url": "http://ava:3008/a2a",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "latency_ms" in body


def test_test_a2a_auth_rejected(client, isolated_delegates_yaml, respx_mock):
    respx_mock.get("http://ava:3008/.well-known/agent-card.json").respond(
        status_code=401,
    )
    r = client.post(
        "/api/delegates/test",
        json={
            "name": "ava",
            "type": "a2a",
            "description": "ping.",
            "url": "http://ava:3008/a2a",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "auth" in body["error"]


def test_test_a2a_unreachable(client, isolated_delegates_yaml, respx_mock):
    respx_mock.get("http://ava:3008/.well-known/agent-card.json").mock(
        side_effect=httpx.ConnectError("connection refused"),
    )
    r = client.post(
        "/api/delegates/test",
        json={
            "name": "ava",
            "type": "a2a",
            "description": "ping.",
            "url": "http://ava:3008/a2a",
        },
    )
    body = r.json()
    assert body["ok"] is False
    assert "unreachable" in body["error"]


def test_test_openai_routes_to_llm_probe(client, isolated_delegates_yaml, respx_mock):
    """The openai test path piggybacks on /api/llm/test's prober so
    error shapes stay consistent across the UI's two test buttons."""
    respx_mock.post("https://gateway/v1/chat/completions").respond(
        status_code=200,
        json={"choices": [{"message": {"content": "pong"}}]},
    )
    r = client.post(
        "/api/delegates/test",
        json={
            "name": "opus",
            "type": "openai",
            "description": "deep think.",
            "url": "https://gateway/v1",
            "model": "claude-opus-4-6",
        },
    )
    body = r.json()
    assert body["ok"] is True


def test_test_schema_rejection_returns_ok_false(client, isolated_delegates_yaml):
    """Schema errors come back as ``{ok: false, error: ...}`` rather
    than 400 — matches /api/llm/test's contract so the UI's test
    button can render the message inline regardless of cause."""
    r = client.post(
        "/api/delegates/test",
        json={"type": "openai", "url": "https://x/v1"},  # missing name + description
    )
    body = r.json()
    assert body["ok"] is False
