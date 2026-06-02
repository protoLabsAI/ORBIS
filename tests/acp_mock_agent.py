"""Minimal ACP *agent* for AcpClient tests — speaks just enough of the wire.

Launched as a subprocess by test_acp_client.py. Handles initialize +
session/new, and on session/prompt: narrates a tool_call, asks permission
(waiting for the client's response), then streams an agent_message_chunk answer
that echoes which permission option the client picked, and finishes the turn.

Not a real agent — no LLM, no file edits. Just exercises the transport.
"""

import json
import sys


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _readline() -> str:
    """Blocking line read with no read-ahead buffering (which `for line in
    sys.stdin` does — it would deadlock this interleaved request/response)."""
    return sys.stdin.readline()


def _read_until_id(target: int) -> dict:
    """Block reading stdin until the response to request ``target`` arrives."""
    while True:
        line = _readline()
        if not line:
            return {}
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if msg.get("id") == target:
            return msg


def main() -> None:
    while True:
        line = _readline()
        if not line:
            return
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
            # 1) narrate a tool call (client should surface the title)
            _send({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": sid,
                "update": {"sessionUpdate": "tool_call", "toolCallId": "t1",
                           "title": "Editing app.py", "status": "pending"}}})
            # 2) ask permission and wait for the client's selection
            _send({"jsonrpc": "2.0", "id": 100, "method": "session/request_permission",
                   "params": {"sessionId": sid, "toolCall": {"toolCallId": "t1"},
                              "options": [
                                  {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                                  {"optionId": "reject", "name": "Reject", "kind": "reject_once"}]}})
            resp = _read_until_id(100)
            chosen = ((resp.get("result") or {}).get("outcome") or {}).get("optionId", "?")
            # 3) stream the answer in two chunks
            for text in ("added a test (", f"perm={chosen})"):
                _send({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": sid,
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"type": "text", "text": text}}}})
            # 4) finish the turn
            _send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    main()
