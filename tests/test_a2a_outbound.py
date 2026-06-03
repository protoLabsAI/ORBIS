"""A2AClient.send must enforce its wall-clock bound.

A buffer-then-answer gateway (Ava routes through the fleet) can hold the
response open. Before this, the `timeout` param was declared but never applied,
so a stuck delegate could wedge the caller — on the voice loop that looked like
ORBIS going silent mid-turn. send() now wraps the consume loop in wait_for.
"""

from __future__ import annotations

import asyncio

import pytest

from a2a_outbound import A2AClient, A2ADispatchError


@pytest.mark.asyncio
async def test_send_enforces_timeout(monkeypatch) -> None:
    client = A2AClient(url="http://ava:3333/a2a", name="ava")

    class _HangingSdk:
        async def send_message(self, _request):
            await asyncio.sleep(30)  # never produces a response
            yield None  # pragma: no cover

    async def _fake_ensure():
        return _HangingSdk()

    monkeypatch.setattr(client, "_ensure_client", _fake_ensure)

    t0 = asyncio.get_event_loop().time()
    with pytest.raises(A2ADispatchError, match="no response within"):
        await client.send("who's online?", timeout=0.2)
    # Bounded — it gave up near the timeout, not after the 30s hang.
    assert asyncio.get_event_loop().time() - t0 < 5.0


@pytest.mark.asyncio
async def test_send_returns_terminal_text(monkeypatch) -> None:
    """A normal completion still returns the answer text + completed state."""
    from a2a.types import Task, TaskState

    client = A2AClient(url="http://ava:3333/a2a", name="ava")

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

    class _OkSdk:
        async def send_message(self, _request):
            task = Task()
            task.id = "t-123"
            task.status.state = TaskState.TASK_STATE_COMPLETED
            yield _Resp(task)

    async def _fake_ensure():
        return _OkSdk()

    monkeypatch.setattr(client, "_ensure_client", _fake_ensure)
    res = await client.send("ping", timeout=5.0)
    assert res.state == "completed"
    assert res.task_id == "t-123"


def test_answer_text_from_status_message() -> None:
    """Ava/workstacean return the answer on the task's status message (no
    artifacts) — extraction must read it, not yield an empty 'ava says —'."""
    from a2a.types import Part, Task, TaskState

    from a2a_outbound import _task_answer_text

    task = Task()
    task.id = "t-1"
    task.status.state = TaskState.TASK_STATE_COMPLETED
    task.status.message.parts.append(Part(text="At standby — ready to route."))
    assert _task_answer_text(task) == "At standby — ready to route."


def test_answer_text_from_history_last_agent_turn() -> None:
    from a2a.types import Message, Part, Role, Task, TaskState

    from a2a_outbound import _task_answer_text

    task = Task()
    task.id = "t-2"
    task.status.state = TaskState.TASK_STATE_COMPLETED
    task.history.append(Message(role=Role.ROLE_USER, parts=[Part(text="q?")]))
    task.history.append(
        Message(role=Role.ROLE_AGENT, parts=[Part(text="the real answer")])
    )
    assert _task_answer_text(task) == "the real answer"


def test_answer_text_prefers_artifact_when_present() -> None:
    """Our own executor's terminal-artifact shape still wins (unchanged)."""
    from a2a.types import Artifact, Part, Task, TaskState

    from a2a_outbound import _task_answer_text

    task = Task()
    task.id = "t-3"
    task.status.state = TaskState.TASK_STATE_COMPLETED
    art = Artifact()
    art.parts.append(Part(text="artifact answer"))
    task.artifacts.append(art)
    task.status.message.parts.append(Part(text="status answer"))
    assert _task_answer_text(task) == "artifact answer"
