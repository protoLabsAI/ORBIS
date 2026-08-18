"""Tests for #695: push-back registration with loopback A2A delegates —
send() actually attaches the TaskPushNotificationConfig (it was silently
swallowed by **_ignored since the a2a-sdk migration), and dispatch defaults
the URL to ORBIS's own /a2a/callback for loopback delegates."""

from __future__ import annotations

import pytest

from a2a_outbound import A2AClient
from agent.delegate_adapters import _default_push_url
from agent.delegates import Delegate


class _Resp:
    def __init__(self, task):
        self._task = task

    def WhichOneof(self, _):
        return "task"

    def HasField(self, f):
        return f == "task"

    @property
    def task(self):
        return self._task


@pytest.mark.asyncio
async def test_send_attaches_push_config(monkeypatch):
    from a2a.types import Task, TaskState

    client = A2AClient(url="http://127.0.0.1:7871/a2a", name="hub")
    captured: list = []

    class _Sdk:
        async def send_message(self, request):
            captured.append(request)
            t = Task()
            t.id = "t-1"
            t.status.state = TaskState.TASK_STATE_COMPLETED
            yield _Resp(t)

    async def _fake_ensure():
        return _Sdk()

    monkeypatch.setattr(client, "_ensure_client", _fake_ensure)
    await client.send("go", timeout=5.0,
                      push_notification_url="http://127.0.0.1:7866/a2a/callback",
                      push_notification_token="tok-1")
    pc = captured[0].configuration.task_push_notification_config
    assert pc.url == "http://127.0.0.1:7866/a2a/callback"
    assert pc.token == "tok-1"


@pytest.mark.asyncio
async def test_send_without_push_url_leaves_config_empty(monkeypatch):
    from a2a.types import Task, TaskState

    client = A2AClient(url="http://127.0.0.1:7871/a2a", name="hub")
    captured: list = []

    class _Sdk:
        async def send_message(self, request):
            captured.append(request)
            t = Task()
            t.id = "t-1"
            t.status.state = TaskState.TASK_STATE_COMPLETED
            yield _Resp(t)

    async def _fake_ensure():
        return _Sdk()

    monkeypatch.setattr(client, "_ensure_client", _fake_ensure)
    await client.send("go", timeout=5.0)
    assert not captured[0].configuration.task_push_notification_config.url


def _delegate(url: str) -> Delegate:
    return Delegate(name="d", description="x", type="a2a", url=url)


def test_default_push_url_loopback(monkeypatch):
    monkeypatch.setenv("ORBIS_BOUND_PORT", "7866")
    assert _default_push_url(_delegate("http://127.0.0.1:7871/a2a")) == \
        "http://127.0.0.1:7866/a2a/callback"
    assert _default_push_url(_delegate("http://localhost:7871/a2a")) == \
        "http://127.0.0.1:7866/a2a/callback"


def test_default_push_url_non_loopback_is_none(monkeypatch):
    monkeypatch.setenv("ORBIS_BOUND_PORT", "7866")
    assert _default_push_url(_delegate("http://100.101.189.45:7873/a2a")) is None
    assert _default_push_url(_delegate("https://agent.example.com/a2a")) is None


def test_default_push_url_requires_bound_port(monkeypatch):
    monkeypatch.delenv("ORBIS_BOUND_PORT", raising=False)
    assert _default_push_url(_delegate("http://127.0.0.1:7871/a2a")) is None
    monkeypatch.setenv("ORBIS_BOUND_PORT", "0")
    assert _default_push_url(_delegate("http://127.0.0.1:7871/a2a")) is None
