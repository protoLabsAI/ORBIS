"""Tests for the LLM-facing check_inbox tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import app as app_module
from agent.tools import check_inbox_handler
from memory import Memory


class FakeParams:
    def __init__(self, arguments: dict[str, Any]):
        self.arguments = arguments
        self.results: list[str] = []

    async def result_callback(self, result: str) -> None:
        self.results.append(result)


@pytest.fixture
def mem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Memory:
    memory = Memory(tmp_path / "orbis.sqlite")
    monkeypatch.setattr(app_module, "get_memory", lambda: memory)
    return memory


@pytest.mark.asyncio
async def test_check_inbox_default_surfaces_now_and_next_then_marks_delivered(
    mem: Memory,
):
    urgent = mem.inbox.add(sender="ops", subject="wake up", body="deploy failed", priority="now")
    normal = mem.inbox.add(sender="github", subject="PR merged", body="main is green")
    later = mem.inbox.add(sender="cron", subject="daily digest", body="all quiet", priority="later")
    params = FakeParams({})

    await check_inbox_handler(params)  # type: ignore[arg-type]

    assert len(params.results) == 1
    assert "wake up" in params.results[0]
    assert "PR merged" in params.results[0]
    assert "daily digest" not in params.results[0]
    assert {row["id"] for row in mem.inbox.list_unread(priority_floor="later")} == {later}
    assert {urgent, normal}.isdisjoint(
        {row["id"] for row in mem.inbox.list_unread(priority_floor="later")},
    )


@pytest.mark.asyncio
async def test_check_inbox_later_floor_surfaces_background_messages(mem: Memory):
    mem.inbox.add(sender="cron", subject="daily digest", body="all quiet", priority="later")
    params = FakeParams({"priority_floor": "later"})

    await check_inbox_handler(params)  # type: ignore[arg-type]

    assert len(params.results) == 1
    assert "daily digest" in params.results[0]
    assert mem.inbox.count_unread(priority_floor="later") == 0


@pytest.mark.asyncio
async def test_check_inbox_include_delivered_reads_without_remarking(mem: Memory):
    msg_id = mem.inbox.add(sender="ops", subject="old alert", body="already handled")
    mem.inbox.mark_delivered([msg_id])
    params = FakeParams({"include_delivered": True})

    await check_inbox_handler(params)  # type: ignore[arg-type]

    assert len(params.results) == 1
    assert "old alert" in params.results[0]
    assert mem.inbox.count_unread(priority_floor="later") == 0
