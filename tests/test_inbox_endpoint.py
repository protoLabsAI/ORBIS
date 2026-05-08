"""Tests for the /api/inbox endpoints — write side (POST), read side
(GET), and the deliver mark (POST /api/inbox/deliver).

Auth covered:
  - owner X-API-Key path (existing require_user)
  - INBOX_INGEST_TOKEN scoped-token path
  - rejection when neither is supplied

Priority handling:
  - default priority = 'next'
  - explicit priority round-trips through the response payload
  - invalid priority returns 400
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
    """Isolated TestClient: tempfile DB so writes don't touch the
    desktop user-data directory's real orbis.sqlite. The cached
    Memory instance in app._memory has to be cleared on entry +
    exit so each test gets a fresh DB."""
    monkeypatch.setenv("ORBIS_DB_PATH", str(tmp_path / "orbis.sqlite"))
    # Reset the lazy-init cache so get_memory() rebuilds against
    # ORBIS_DB_PATH for this test. open_db() re-resolves the path
    # on each call, so just clearing the module-level handle is enough.
    monkeypatch.setattr(app_module, "_memory", None)
    c = TestClient(app_module.app)
    app_module.app.dependency_overrides[require_user] = _make_user
    yield c
    app_module.app.dependency_overrides.clear()
    app_module._memory = None  # ensure no leak into next test


# --- POST /api/inbox -------------------------------------------------------


def test_post_inbox_owner_key_path_writes(client: TestClient):
    """Owner-key auth (require_user override returns DEFAULT_USER) →
    write succeeds, returns id."""
    r = client.post("/api/inbox", json={
        "sender": "webhook:slack",
        "subject": "PR landed",
        "body": "PR #123 just merged",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["id"], int) and body["id"] > 0


def test_post_inbox_default_priority_is_next(client: TestClient):
    client.post("/api/inbox", json={
        "sender": "x", "subject": "s", "body": "b",
    })
    r = client.get("/api/inbox?unread_only=1")
    msgs = r.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["priority"] == "next"


def test_post_inbox_explicit_priority_roundtrips(client: TestClient):
    for p in ("now", "next", "later"):
        client.post("/api/inbox", json={
            "sender": "x", "subject": p, "body": "b", "priority": p,
        })
    rows = client.get("/api/inbox?unread_only=1&priority_floor=later").json()["messages"]
    by_priority = {r["subject"]: r["priority"] for r in rows}
    assert by_priority == {"now": "now", "next": "next", "later": "later"}


def test_post_inbox_unknown_priority_400(client: TestClient):
    r = client.post("/api/inbox", json={
        "sender": "x", "subject": "s", "body": "b", "priority": "urgent",
    })
    assert r.status_code == 400
    assert "priority" in r.json()["detail"].lower()


def test_post_inbox_missing_required_fields_400(client: TestClient):
    r = client.post("/api/inbox", json={"body": "no sender or subject"})
    assert r.status_code == 400
    assert "sender" in r.json()["detail"] or "subject" in r.json()["detail"]


def test_post_inbox_ingest_token_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    """When the owner-key path fails (require_user dep not registered),
    the X-API-Key header matching INBOX_INGEST_TOKEN should still let
    the write through. Multi-tenant guard simulated by making
    user_registry.single_user_mode return False so the owner-key
    path can't fall through."""
    monkeypatch.setenv("INBOX_INGEST_TOKEN", "scoped-token")
    # Reload app module env-driven constants — INBOX_INGEST_TOKEN is
    # read at import time. Direct overwrite keeps the test simple.
    monkeypatch.setattr(app_module, "INBOX_INGEST_TOKEN", "scoped-token")
    monkeypatch.setattr(
        app_module.user_registry, "single_user_mode", lambda: False,
    )
    monkeypatch.setattr(
        app_module.user_registry, "resolve", lambda _k: None,
    )
    r = client.post(
        "/api/inbox",
        json={"sender": "webhook", "subject": "s", "body": "b"},
        headers={"X-API-Key": "scoped-token"},
    )
    assert r.status_code == 200


def test_post_inbox_rejects_unauthorized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    """No owner key, no ingest token → 401."""
    monkeypatch.setattr(app_module, "INBOX_INGEST_TOKEN", "")
    monkeypatch.setattr(
        app_module.user_registry, "single_user_mode", lambda: False,
    )
    monkeypatch.setattr(
        app_module.user_registry, "resolve", lambda _k: None,
    )
    r = client.post(
        "/api/inbox",
        json={"sender": "x", "subject": "s", "body": "b"},
    )
    assert r.status_code == 401


# --- GET /api/inbox --------------------------------------------------------


def test_get_inbox_default_lists_all(client: TestClient):
    client.post("/api/inbox", json={"sender": "a", "subject": "1", "body": "x"})
    client.post("/api/inbox", json={"sender": "b", "subject": "2", "body": "y"})
    r = client.get("/api/inbox")
    body = r.json()
    assert len(body["messages"]) == 2
    assert body["unread_count"] == 2


def test_get_inbox_unread_only_filters_delivered(client: TestClient):
    a = client.post("/api/inbox", json={"sender": "x", "subject": "a", "body": "A"}).json()["id"]
    client.post("/api/inbox", json={"sender": "x", "subject": "b", "body": "B"})
    client.post("/api/inbox/deliver", json={"ids": [a]})

    body = client.get("/api/inbox?unread_only=1").json()
    subjects = [m["subject"] for m in body["messages"]]
    assert subjects == ["b"]


def test_get_inbox_priority_floor_filters(client: TestClient):
    for p in ("now", "next", "later"):
        client.post("/api/inbox", json={
            "sender": "x", "subject": p, "body": p, "priority": p,
        })
    body = client.get("/api/inbox?unread_only=1&priority_floor=now").json()
    assert {m["subject"] for m in body["messages"]} == {"now"}

    body = client.get("/api/inbox?unread_only=1&priority_floor=next").json()
    assert {m["subject"] for m in body["messages"]} == {"now", "next"}


# --- POST /api/inbox/deliver ----------------------------------------------


def test_deliver_marks_messages(client: TestClient):
    a = client.post("/api/inbox", json={"sender": "x", "subject": "a", "body": "A"}).json()["id"]
    b = client.post("/api/inbox", json={"sender": "x", "subject": "b", "body": "B"}).json()["id"]
    r = client.post("/api/inbox/deliver", json={"ids": [a, b]})
    assert r.status_code == 200
    assert r.json()["delivered"] == 2

    # Re-deliver is a no-op (idempotent).
    r = client.post("/api/inbox/deliver", json={"ids": [a, b]})
    assert r.json()["delivered"] == 0


def test_deliver_rejects_non_list_ids(client: TestClient):
    r = client.post("/api/inbox/deliver", json={"ids": "not a list"})
    assert r.status_code == 400


def test_deliver_rejects_non_integer_ids(client: TestClient):
    r = client.post("/api/inbox/deliver", json={"ids": ["abc"]})
    assert r.status_code == 400
