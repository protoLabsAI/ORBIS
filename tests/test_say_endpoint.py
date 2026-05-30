"""Tests for POST /api/say — external "ping ORBIS to speak" (orbis-wox).

Routes external text into the DeliveryController: spoken now if a session
is live (urgency-gated), else stashed for replay on next connect.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
from agent.delivery import Priority


class FakeDelivery:
    def __init__(self):
        self.calls: list[tuple] = []

    async def deliver(self, phrase, *, priority=None, source=None, **kw):
        self.calls.append((phrase, priority, source))


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ORBIS_DB_PATH", str(tmp_path / "orbis.sqlite"))
    monkeypatch.setattr(app_module, "_memory", None)
    c = TestClient(app_module.app)
    yield c
    app_module._memory = None


def _set_live_delivery(monkeypatch, delivery) -> None:
    monkeypatch.setattr(
        app_module, "user_state_for",
        lambda _uid: SimpleNamespace(active_delivery=delivery),
    )


def test_missing_text_is_400(client: TestClient) -> None:
    assert client.post("/api/say", json={}).status_code == 400


def test_bad_urgency_is_400(client: TestClient) -> None:
    r = client.post("/api/say", json={"text": "hi", "urgency": "screaming"})
    assert r.status_code == 400


def test_delivered_when_session_live(client: TestClient, monkeypatch) -> None:
    fake = FakeDelivery()
    _set_live_delivery(monkeypatch, fake)
    r = client.post("/api/say", json={"text": "the build went green"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "delivered": True}
    assert len(fake.calls) == 1
    phrase, priority, source = fake.calls[0]
    assert phrase == "the build went green"
    assert priority == Priority.TIME_SENSITIVE  # default urgency=normal
    assert source is None


def test_urgent_maps_to_critical(client: TestClient, monkeypatch) -> None:
    fake = FakeDelivery()
    _set_live_delivery(monkeypatch, fake)
    r = client.post("/api/say", json={"text": "fire", "urgency": "urgent"})
    assert r.status_code == 200
    assert fake.calls[0][1] == Priority.CRITICAL


def test_source_is_passed_for_attribution(client: TestClient, monkeypatch) -> None:
    fake = FakeDelivery()
    _set_live_delivery(monkeypatch, fake)
    client.post("/api/say", json={"text": "done", "source": "ava"})
    assert fake.calls[0][2] == "ava"


def test_stashed_when_no_live_session(client: TestClient, monkeypatch) -> None:
    _set_live_delivery(monkeypatch, None)
    stashed: list = []
    monkeypatch.setattr(
        "agent.session_store.stash_delivery",
        lambda uid, item: stashed.append((uid, item)),
    )
    r = client.post("/api/say", json={"text": "later thing", "urgency": "low"})
    assert r.status_code == 200
    assert r.json()["stashed"] is True
    assert len(stashed) == 1
    _uid, item = stashed[0]
    assert item["phrase"] == "later thing"
    assert item["priority"] == Priority.ACTIVE.value
