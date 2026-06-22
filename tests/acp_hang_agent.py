"""Mock ACP agent that HANGS on session/prompt — for the dispatch-cancel test.

Like acp_reaping_agent it forks a sleep grandchild and writes both pids to
``<cwd>/pids.json``, and answers initialize + session/new normally. But on
session/prompt it deliberately never sends a result (it keeps reading stdin so it
can absorb a session/cancel), so the client's prompt() stays pending until the
caller cancels — exercising the adapter's cancel→drop+kill_now reaping path.
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
            # Never respond — keep the turn pending. (Keep looping so we can
            # absorb the client's session/cancel notification without erroring.)
            continue
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    main()
