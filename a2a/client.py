"""A2A outbound client — JSON-RPC 2.0 dispatch.

Two call styles, both returning the assistant text:

  - `dispatch_message(url, …)` — synchronous `message/send`; one HTTP
    round-trip, final result only.
  - `dispatch_message_stream(url, …, progress_callback=…)` — streaming
    `message/stream` over SSE; consumes TaskStatusUpdateEvent +
    TaskArtifactUpdateEvent frames, reports progress via the callback,
    returns final text at end of stream.

Streaming is the preferred path for delegated long-running work — the
caller wires `progress_callback` into the voice pipeline so intermediate
status narrates in-flight. Falls back to `dispatch_message` on SSE errors.

Delegate configuration in `agent/delegates.py` picks the path.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable

import httpx

from agent.tracing import active_trace, propagation_headers

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


class A2ADispatchError(RuntimeError):
    """Raised when the remote agent returns an error or the response is
    malformed. Caught by the tool wrapper so it becomes a spoken error."""


async def dispatch_message(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    user_text: str = "",
    context_id: str | None = None,
    timeout: float = 60.0,
) -> str:
    """POST `message/send` to `url`, return the assistant text from the
    first artifact's first text part.

    Raises A2ADispatchError on non-2xx, JSON-RPC error, or missing text.
    """
    if not user_text:
        raise A2ADispatchError("empty user_text")
    rpc_id = str(uuid.uuid4())
    context_id = context_id or str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "message/send",
        "params": {
            "contextId": context_id,
            "message": {
                "messageId": str(uuid.uuid4()),  # A2A spec — required field
                "role": "user",
                "parts": [{"kind": "text", "text": user_text}],
            },
        },
    }
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    # Cross-fleet trace propagation (see docs/reference/tracing-contract.md).
    req_headers.update(propagation_headers(trace=active_trace()))

    logger.info(f"[a2a] dispatch → {url}")
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=req_headers)

    if resp.status_code != 200:
        raise A2ADispatchError(
            f"HTTP {resp.status_code} from {url} — {resp.text[:200]}"
        )

    try:
        body = resp.json()
    except Exception as e:
        raise A2ADispatchError(f"non-JSON response from {url} ({e})") from e

    if "error" in body:
        raise A2ADispatchError(f"{body['error']}")

    result = body.get("result") or {}
    text = _first_task_artifact_text(result) or _status_message_text(result)
    if text:
        return text
    raise A2ADispatchError(f"response from {url} had no text reply")


# ---------------------------------------------------------------------------
# Streaming variant (A2A `message/stream`, SSE)
# ---------------------------------------------------------------------------

async def dispatch_message_stream(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    user_text: str = "",
    context_id: str | None = None,
    timeout: float = 120.0,
    progress_callback: ProgressCallback | None = None,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
) -> str:
    """POST `message/stream` and consume SSE events until terminal.

    Per A2A spec (https://a2a-protocol.org/latest/topics/streaming-and-async/):
      - Response is `Content-Type: text/event-stream`.
      - Each `data:` line carries a JSON-RPC 2.0 object whose `result`
        is one of: Task, Message, TaskStatusUpdateEvent,
        TaskArtifactUpdateEvent.
      - The stream ends when `TaskStatusUpdateEvent.final == true` or
        a terminal state (completed / failed / cancelled) is reached.

    If `progress_callback` is set, we invoke it for each
    TaskStatusUpdateEvent that carries a human-readable message
    — lets the caller narrate "still working" style updates to the user.

    If `push_notification_url` is provided, include a
    `pushNotificationConfig` on the initial request so the remote agent
    can call us back if the stream drops (A2A D17 integration).

    On transport error, the function raises A2ADispatchError — the
    caller is expected to decide whether to fall back to sync dispatch.
    """
    if not user_text:
        raise A2ADispatchError("empty user_text")

    rpc_id = str(uuid.uuid4())
    context_id = context_id or str(uuid.uuid4())
    params: dict = {
        "contextId": context_id,
        "message": {
            "messageId": str(uuid.uuid4()),  # A2A spec — required field
            "role": "user",
            "parts": [{"kind": "text", "text": user_text}],
        },
    }
    if push_notification_url:
        params["configuration"] = {
            "pushNotificationConfig": {
                "url": push_notification_url,
                **({"token": push_notification_token} if push_notification_token else {}),
            },
        }
    payload = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "message/stream",
        "params": params,
    }
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if headers:
        req_headers.update(headers)
    # Cross-fleet trace propagation (see docs/reference/tracing-contract.md).
    req_headers.update(propagation_headers(trace=active_trace()))

    logger.info(f"[a2a/stream] dispatch → {url}")
    final_text: str | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload, headers=req_headers) as resp:
            if resp.status_code != 200:
                body_text = (await resp.aread()).decode(errors="replace")
                raise A2ADispatchError(
                    f"HTTP {resp.status_code} from {url} — {body_text[:200]}"
                )
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except Exception as e:
                    logger.warning(f"[a2a/stream] unparseable SSE line: {e}")
                    continue
                if "error" in event:
                    raise A2ADispatchError(f"{event['error']}")
                result = event.get("result") or {}
                kind = result.get("kind") or result.get("type") or ""

                # Status update — narrate mid-task text as progress; on a
                # terminal update, treat `status.message` as the final reply
                # if no artifact has been streamed (A2A spec allows either).
                if kind in ("task-status-update", "taskStatusUpdate", "status-update"):
                    msg = _status_message_text(result)
                    terminal = result.get("final") or _is_terminal(result.get("status") or {})
                    if msg:
                        if terminal:
                            if not final_text:
                                final_text = msg
                        elif progress_callback:
                            try:
                                await progress_callback(msg)
                            except Exception as e:
                                logger.warning(f"[a2a/stream] progress_callback raised: {e}")
                    if terminal:
                        break
                    continue

                # Artifact chunk — accumulate text.
                if kind in ("task-artifact-update", "taskArtifactUpdate", "artifact-update"):
                    text = _artifact_text(result)
                    if text:
                        final_text = (final_text or "") + text
                    continue

                # Full Task snapshot — extract artifact text, or fall back
                # to status.message if artifacts are empty.
                if kind == "task":
                    text = _first_task_artifact_text(result) or _status_message_text(result)
                    if text:
                        final_text = text
                    if _is_terminal(result.get("status") or {}):
                        break
                    continue

                # Unknown kind — log at debug, keep going.
                logger.debug(f"[a2a/stream] unknown event kind: {kind!r}")

    if final_text:
        return final_text
    raise A2ADispatchError(f"stream from {url} ended without a text artifact")


def _status_message_text(event: dict) -> str | None:
    """Extract plain text from a TaskStatusUpdateEvent's status.message."""
    status = event.get("status") or {}
    message = status.get("message") or {}
    parts = message.get("parts") or []
    for part in parts:
        kind = part.get("kind") or part.get("type")
        if kind == "text" and part.get("text"):
            return part["text"]
    return None


def _artifact_text(event: dict) -> str | None:
    """Extract text from a TaskArtifactUpdateEvent.artifact."""
    artifact = event.get("artifact") or {}
    for part in artifact.get("parts") or []:
        kind = part.get("kind") or part.get("type")
        if kind == "text" and part.get("text"):
            return part["text"]
    return None


def _first_task_artifact_text(task: dict) -> str | None:
    for art in task.get("artifacts") or []:
        for part in art.get("parts") or []:
            kind = part.get("kind") or part.get("type")
            if kind == "text" and part.get("text"):
                return part["text"]
    return None


def _is_terminal(status_or_event: dict) -> bool:
    state = (status_or_event.get("state") or "").lower()
    return state in ("completed", "failed", "cancelled", "rejected")
