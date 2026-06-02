"""Tests for the ACP client transport + the type=acp delegate parse.

Drives ``AcpClient`` against a mock ACP agent (tests/acp_mock_agent.py) launched
as a real subprocess, so the JSON-RPC-over-stdio loop, streaming updates, and
permission round-trip are exercised end to end without a real coding agent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from acp import AcpClient, AcpError

_MOCK = str(Path(__file__).parent / "acp_mock_agent.py")


@pytest.mark.asyncio
async def test_prompt_roundtrip_streams_and_auto_allows(tmp_path):
    narrated: list[str] = []

    async def progress(text: str) -> None:
        narrated.append(text)

    client = AcpClient(sys.executable, [_MOCK], cwd=str(tmp_path), name="mock")
    try:
        answer = await client.prompt("add a test", progress_callback=progress)
    finally:
        await client.close()

    # agent_message_chunks accumulated into the answer
    assert "added a test" in answer
    # the client auto-allowed the permission request → mock echoed the choice
    assert "perm=allow" in answer
    # the tool_call title was narrated via progress_callback (not the answer)
    assert "Editing app.py" in narrated
    assert "Editing app.py" not in answer


@pytest.mark.asyncio
async def test_followup_turn_reuses_session(tmp_path):
    client = AcpClient(sys.executable, [_MOCK], cwd=str(tmp_path), name="mock")
    try:
        first = await client.prompt("one")
        second = await client.prompt("two")
    finally:
        await client.close()
    assert "added a test" in first
    assert "added a test" in second


@pytest.mark.asyncio
async def test_missing_binary_raises(tmp_path):
    client = AcpClient("definitely-not-a-real-binary-xyz", cwd=str(tmp_path))
    with pytest.raises(AcpError):
        await client.prompt("hi")


@pytest.mark.asyncio
async def test_missing_workdir_raises():
    client = AcpClient(sys.executable, [_MOCK], cwd="/no/such/dir/orbis-acp")
    with pytest.raises(AcpError):
        await client.prompt("hi")


# --- registry parse ---------------------------------------------------------


def test_parse_acp_delegate(monkeypatch, tmp_path):
    from agent.delegates import _parse_entry

    monkeypatch.setenv("ORBIS_ACP_DIR", str(tmp_path))
    d = _parse_entry({
        "name": "proto",
        "type": "acp",
        "description": "voice-drive protoCLI in this repo",
        "command": "proto",
        "args": ["--acp"],
        "workdir": "${ORBIS_ACP_DIR}",
    })
    assert d is not None
    assert d.type == "acp"
    assert d.command == "proto"
    assert d.args == ["--acp"]
    assert d.workdir == str(tmp_path)


def test_parse_acp_requires_command_and_workdir():
    from agent.delegates import _parse_entry

    assert _parse_entry({
        "name": "x", "type": "acp", "description": "d", "workdir": "/tmp",
    }) is None  # no command
    assert _parse_entry({
        "name": "x", "type": "acp", "description": "d", "command": "proto",
    }) is None  # no workdir
