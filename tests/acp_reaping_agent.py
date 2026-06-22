"""Mock ACP *agent* that spawns a long-lived GRANDCHILD, for the reaping tests.

Mirrors how a real ACP adapter (`npx …claude-agent-acp`) forks a backend: on
launch this writes its own pid + a `time.sleep` grandchild's pid to
``<cwd>/pids.json``, then speaks just enough of the ACP wire (initialize +
session/new + a trivial prompt turn). The grandchild reparents to init if it
isn't group-killed — which is exactly the leak the process-group reaping fixes.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    # Spawn a grandchild that outlives a single-PID terminate(). It inherits this
    # process's group (the client launches us with start_new_session=True, so we
    # are the group leader), so a killpg on our group must take it down too.
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    (Path(os.getcwd()) / "pids.json").write_text(
        json.dumps({"agent": os.getpid(), "grandchild": grandchild.pid})
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": 1, "agentCapabilities": {}}})
        elif method == "session/new":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "sess-1"}})
        elif method == "session/prompt":
            sid = msg["params"]["sessionId"]
            _send({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": sid,
                "update": {"sessionUpdate": "agent_message_chunk",
                           "content": {"type": "text", "text": "ok"}}}})
            _send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    main()
