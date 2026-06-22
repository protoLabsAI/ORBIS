"""Process-group reaping + initialize-only handshake for the ACP client.

A real ACP adapter forks a backend (`npx …claude-agent-acp` → node); ORBIS
launches the agent in its own process group (start_new_session=True) so teardown
can SIGKILL the whole tree instead of leaking the reparented backend. These
tests drive a mock agent that spawns a `time.sleep(300)` grandchild and assert
the grandchild dies with the agent.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from acp import AcpClient

_REAPER = str(Path(__file__).parent / "acp_reaping_agent.py")


def _alive(pid: int) -> bool:
    """True if `pid` exists (and isn't a reaped zombie). os.kill(pid, 0) raises
    ProcessLookupError once the process is gone and reaped."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


async def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    """Poll until `pid` is gone (group kill + reap is async)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not _alive(pid):
            return True
        await asyncio.sleep(0.05)
    return not _alive(pid)


def _read_pids(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "pids.json").read_text())


@pytest.mark.asyncio
async def test_close_reaps_the_whole_process_group(tmp_path):
    client = AcpClient(sys.executable, [_REAPER], cwd=str(tmp_path), name="reap")
    await client._ensure_started()
    pids = _read_pids(tmp_path)
    agent_pid, grandchild_pid = pids["agent"], pids["grandchild"]
    assert _alive(agent_pid) and _alive(grandchild_pid)

    await client.close()

    # Both the agent AND its grandchild die — the grandchild is the leak a
    # single-PID terminate() would have orphaned.
    assert await _wait_dead(agent_pid), "agent survived close()"
    assert await _wait_dead(grandchild_pid), "grandchild leaked past close()"


@pytest.mark.asyncio
async def test_kill_now_is_synchronous_and_reaps_the_group(tmp_path):
    client = AcpClient(sys.executable, [_REAPER], cwd=str(tmp_path), name="reap")
    await client._ensure_started()
    pids = _read_pids(tmp_path)

    # No await — safe to call from a CancelledError handler.
    client.kill_now()

    assert await _wait_dead(pids["agent"]), "agent survived kill_now()"
    assert await _wait_dead(pids["grandchild"]), "grandchild survived kill_now()"


@pytest.mark.asyncio
async def test_handshake_does_not_open_a_session(tmp_path):
    client = AcpClient(sys.executable, [_REAPER], cwd=str(tmp_path), name="reap")
    try:
        await client.handshake()
        # initialize ran (process is up) but session/new did NOT — a probe is inert.
        assert client._proc is not None and client._proc.returncode is None
        assert client._session_id is None
    finally:
        await client.close()
