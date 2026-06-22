"""AcpAdapter behavior that drives a real agent subprocess: the initialize-only
health probe and the cancel→drop+reap path. (Pure registry/parse unit tests live
in test_delegate_adapters.py; the transport round-trip in test_acp_client.py.)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from agent.delegate_adapters import AcpAdapter
from agent.delegates import Delegate

_MOCK = str(Path(__file__).parent / "acp_mock_agent.py")
_HANG = str(Path(__file__).parent / "acp_hang_agent.py")


def _delegate(args: list[str], workdir: str, name: str = "probe-me") -> Delegate:
    return Delegate(
        name=name, description="acp test", type="acp",
        command=sys.executable, args=args, workdir=workdir,
    )


def _alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


async def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not _alive(pid):
            return True
        await asyncio.sleep(0.05)
    return not _alive(pid)


@pytest.mark.asyncio
async def test_probe_runs_a_real_handshake(tmp_path):
    """A real ACP agent → ok=True (the probe actually spoke `initialize`)."""
    adapter = AcpAdapter()
    result = await adapter.probe(_delegate([_MOCK], str(tmp_path)))
    assert result == {"ok": True}
    # The probe used an EPHEMERAL client — it must not leave one pooled.
    assert adapter._clients == {}


@pytest.mark.asyncio
async def test_probe_fails_when_command_cannot_speak_acp(tmp_path):
    """On PATH (the static which check passes) but NOT an ACP agent — the binary
    exits without a handshake. The old which+dir-only probe false-greened this;
    the handshake catches it."""
    adapter = AcpAdapter()
    # `python -c pass` exits immediately → no initialize response.
    result = await adapter.probe(_delegate(["-c", "pass"], str(tmp_path)), timeout=4.0)
    assert result["ok"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_probe_fails_command_not_on_path(tmp_path):
    adapter = AcpAdapter()
    d = Delegate(name="x", description="d", type="acp",
                 command="definitely-not-a-real-binary-xyz", workdir=str(tmp_path))
    result = await adapter.probe(d)
    assert result["ok"] is False
    assert "not on PATH" in result["error"]


@pytest.mark.asyncio
async def test_dispatch_cancel_drops_and_reaps(tmp_path):
    """A cancelled dispatch (e.g. an orchestrator's wait_for watchdog firing on a
    wedged coding turn) must evict the pooled client AND SIGKILL its process
    group — not leave a live, unreachable agent behind."""
    adapter = AcpAdapter()
    delegate = _delegate([_HANG], str(tmp_path), name="hanger")

    # Drive one turn that never completes, then cancel it from outside (what
    # orchestrate's asyncio.wait_for does).
    task = asyncio.create_task(adapter.dispatch(delegate, "do something slow"))
    # Wait until the agent is up and has forked its grandchild.
    pids_file = tmp_path / "pids.json"
    for _ in range(100):
        if pids_file.exists():
            break
        await asyncio.sleep(0.05)
    pids = json.loads(pids_file.read_text())
    assert _alive(pids["agent"]) and _alive(pids["grandchild"])

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Client evicted from the pool…
    assert adapter._clients == {}
    # …and the whole tree reaped.
    assert await _wait_dead(pids["agent"]), "agent survived dispatch cancel"
    assert await _wait_dead(pids["grandchild"]), "grandchild leaked past dispatch cancel"
