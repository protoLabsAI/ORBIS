"""Tests for GET /api/delegations and OutboundTasksDAL.recent()."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
import auth.users as auth_users
from auth import require_user
from auth.users import User
from memory import Memory


@pytest.fixture
def mem(tmp_path):
    m = Memory(tmp_path / "t.sqlite")
    yield m
    m.close()


def _age(mem, task_id: str, hours: float) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    mem.outbound.conn.execute(
        "UPDATE outbound_tasks SET updated_at = ? WHERE task_id = ?", (ts, task_id)
    )
    mem.outbound.conn.commit()


# --- DAL.recent -------------------------------------------------------------


def test_recent_includes_live_and_fresh_terminal_newest_first(mem):
    mem.outbound.record(task_id="a", delegate="hub", query="qa")
    mem.outbound.record(task_id="b", delegate="hub", query="qb")
    mem.outbound.update("b", status="completed", result="done")
    ids = [r["task_id"] for r in mem.outbound.recent()]
    assert ids == ["b", "a"]


def test_recent_drops_old_terminal_but_keeps_stale_live(mem):
    mem.outbound.record(task_id="old-done", delegate="hub", query="q")
    mem.outbound.update("old-done", status="completed")
    _age(mem, "old-done", 48)
    mem.outbound.record(task_id="stale-live", delegate="hub", query="q",
                        status="working")
    _age(mem, "stale-live", 48)
    ids = {r["task_id"] for r in mem.outbound.recent()}
    assert ids == {"stale-live"}


def test_recent_is_bounded_and_clamped(mem):
    for i in range(130):
        mem.outbound.record(task_id=f"t{i}", delegate="hub", query="q")
    assert len(mem.outbound.recent(5)) == 5
    assert len(mem.outbound.recent(10_000)) == 100
    assert len(mem.outbound.recent(0)) == 1  # clamped up, never unbounded/negative


# --- endpoint ---------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORBIS_DB_PATH", str(tmp_path / "orbis.sqlite"))
    monkeypatch.setattr(app_module, "_memory", None)
    c = TestClient(app_module.app)
    app_module.app.dependency_overrides[require_user] = lambda: User(
        id="owner", display_name="Owner", api_key_hash="hash"
    )
    yield c
    app_module.app.dependency_overrides.clear()
    app_module._memory = None


def test_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("ORBIS_DB_PATH", str(tmp_path / "orbis.sqlite"))
    monkeypatch.setattr(app_module, "_memory", None)
    # load_users() rebinds auth.users.user_registry, so patch the live one
    # that require_user actually consults (not app's import-time alias).
    live = auth_users.user_registry
    monkeypatch.setattr(live, "single_user_mode", lambda: False)
    monkeypatch.setattr(live, "resolve", lambda _k: None)
    r = TestClient(app_module.app).get("/api/delegations")
    assert r.status_code == 401
    app_module._memory = None


def test_shape_and_query_truncation(client):
    mem = app_module.get_memory()
    mem.outbound.record(task_id="t-1", delegate="protoagent",
                        query="x" * 400, status="working")
    r = client.get("/api/delegations")
    assert r.status_code == 200
    (task,) = r.json()["tasks"]
    assert set(task) == {"task_id", "delegate", "query", "status", "result",
                         "created_at", "updated_at"}
    assert task["delegate"] == "protoagent"
    assert task["status"] == "working"
    assert task["result"] is None
    assert len(task["query"]) <= 160


def test_limit_is_bounded(client):
    mem = app_module.get_memory()
    for i in range(30):
        mem.outbound.record(task_id=f"t{i}", delegate="hub", query="q")
    assert len(client.get("/api/delegations").json()["tasks"]) == 20
    assert len(client.get("/api/delegations?limit=3").json()["tasks"]) == 3
    assert client.get("/api/delegations?limit=1000").status_code == 422
