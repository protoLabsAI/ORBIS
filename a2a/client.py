"""A2A outbound client — JSON-RPC 2.0 dispatch.

Two layers:

  - **`A2AClient`** — a first-class, per-delegate client (the preferred
    surface). Discovers + caches the delegate's Agent Card, picks the
    transport from the card's ``preferredTransport`` (JSON-RPC today; gRPC /
    REST are a future drop-in behind the same ``send()``), negotiates
    streaming from the card's ``capabilities``, and threads a **persistent
    ``contextId``** across calls so a delegate retains context turn-to-turn
    (the basis for the D1 orchestrate loop). ``send()`` returns an
    ``A2AResult`` carrying the task ``state`` / ``task_id`` / ``context_id``
    so callers can detect ``input-required`` and continue a task.

  - **Stateless transport functions** — `dispatch_message` (sync) and
    `dispatch_message_stream` (SSE `message/stream`, `progress_callback`).
    These are the wire primitives `A2AClient` calls; kept as a thin public
    surface for back-compat.

Streaming is preferred for long-running delegated work — the caller wires
`progress_callback` into the voice pipeline so intermediate status narrates
in-flight. Falls back to sync on SSE errors. Delegate configuration in
`agent/delegates.py` constructs the client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

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


# Terminal task states per the A2A spec (a2a-protocol.org §4.1.3). The spec
# spells it "canceled" (one l); we also accept "cancelled" since some
# implementations use the British spelling.
TERMINAL_STATES: frozenset[str] = frozenset(
    {"completed", "failed", "canceled", "cancelled", "rejected"}
)


def _is_terminal(status_or_event: dict) -> bool:
    state = (status_or_event.get("state") or "").lower()
    return state in TERMINAL_STATES


# ---------------------------------------------------------------------------
# First-class client (A2AClient)
# ---------------------------------------------------------------------------

# Interrupted (non-terminal) states where the agent is waiting on the client.
_INTERRUPTED_STATES: frozenset[str] = frozenset({"input-required", "auth-required"})


class A2ATransport(str, Enum):
    """A2A wire transports (spec §3.2). Only JSON-RPC is implemented; the
    others are recognised so the client can pick from an Agent Card's
    ``preferredTransport`` and fail clearly (rather than mis-dispatch) until
    a transport is added."""

    JSONRPC = "JSONRPC"
    GRPC = "GRPC"
    HTTP_JSON = "HTTP+JSON"


@dataclass
class A2AResult:
    """Outcome of one ``A2AClient.send``. ``text`` is the assistant reply.
    ``state`` / ``task_id`` / ``context_id`` come from the A2A Task when the
    delegate runs the message as a task (vs a direct Message). ``input_required``
    is True when the delegate paused to ask us for more — the caller continues
    the task by sending again with the same ``task_id`` + ``context_id`` (PR-2)."""

    text: str
    state: str | None = None
    task_id: str | None = None
    context_id: str | None = None
    input_required: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.state is None or (self.state or "").lower() in TERMINAL_STATES


def _parse_result_object(result: dict, *, fallback_context_id: str | None) -> A2AResult:
    """Turn a JSON-RPC ``result`` (an A2A Task or Message) into an A2AResult."""
    text = _first_task_artifact_text(result) or _status_message_text(result) or ""
    status = result.get("status") or {}
    state = status.get("state")
    task_id = result.get("id") or result.get("taskId")
    context_id = result.get("contextId") or fallback_context_id
    input_required = (state or "").lower() in _INTERRUPTED_STATES
    return A2AResult(
        text=text,
        state=state,
        task_id=task_id,
        context_id=context_id,
        input_required=input_required,
    )


async def _post_jsonrpc(
    url: str,
    method: str,
    params: dict,
    *,
    headers: dict[str, str] | None,
    timeout: float,
) -> dict:
    """POST one JSON-RPC 2.0 request and return its ``result`` object.

    Shared by the A2AClient methods (message/send, tasks/get, …). Raises
    A2ADispatchError on transport error, JSON-RPC error, or non-JSON body."""
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": params,
    }
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req_headers.update(propagation_headers(trace=active_trace()))

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=req_headers)
    if resp.status_code != 200:
        raise A2ADispatchError(f"HTTP {resp.status_code} from {url} — {resp.text[:200]}")
    try:
        body = resp.json()
    except Exception as e:
        raise A2ADispatchError(f"non-JSON response from {url} ({e})") from e
    if "error" in body:
        raise A2ADispatchError(f"{body['error']}")
    return body.get("result") or {}


class A2AClient:
    """First-class A2A client bound to ONE delegate.

    Holds a sticky ``context_id`` (multi-turn continuity) and a lazily-fetched,
    cached Agent Card (capability + transport negotiation). Construct one per
    delegate; for a multi-step orchestration run construct a fresh instance per
    delegate so contexts don't cross between runs.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        card_origin: str | None = None,
        name: str | None = None,
        context_id: str | None = None,
    ) -> None:
        self.url = url
        self.name = name or url
        self._headers = dict(headers or {})
        self._card_origin = card_origin
        self._context_id = context_id or str(uuid.uuid4())
        self._card: dict | None = None
        self._card_fetched = False

    @property
    def context_id(self) -> str:
        return self._context_id

    async def agent_card(self, *, timeout: float = 5.0) -> dict | None:
        """Lazily GET + cache ``{card_origin}/.well-known/agent-card.json``.
        Tolerant: returns None (and caches that) on any failure — capability
        checks then fall back to conservative defaults."""
        if self._card_fetched:
            return self._card
        self._card_fetched = True
        if not self._card_origin:
            return None
        card_url = f"{self._card_origin}/.well-known/agent-card.json"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(card_url, headers=self._headers)
            if r.status_code == 200:
                self._card = r.json()
            else:
                logger.debug(f"[a2a] {self.name} agent-card HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001 — discovery is best-effort
            logger.debug(f"[a2a] {self.name} agent-card fetch failed: {e}")
        return self._card

    async def _capabilities(self) -> dict:
        card = await self.agent_card()
        return (card or {}).get("capabilities") or {}

    async def supports_streaming(self) -> bool:
        return bool((await self._capabilities()).get("streaming"))

    async def preferred_transport(self) -> A2ATransport:
        """Transport from the card's ``preferredTransport`` (default JSON-RPC
        per spec when absent). gRPC/REST recognised but not yet implemented."""
        card = await self.agent_card()
        raw = (card or {}).get("preferredTransport") or A2ATransport.JSONRPC.value
        try:
            return A2ATransport(raw)
        except ValueError:
            logger.debug(f"[a2a] {self.name} unknown preferredTransport {raw!r}")
            return A2ATransport.JSONRPC

    async def send(
        self,
        text: str,
        *,
        context_id: str | None = None,
        task_id: str | None = None,
        progress_callback: ProgressCallback | None = None,
        prefer_stream: bool | None = None,
        timeout: float = 60.0,
        push_notification_url: str | None = None,
        push_notification_token: str | None = None,
    ) -> A2AResult:
        """Send a message to the delegate and return an ``A2AResult``.

        Transport is taken from the Agent Card (JSON-RPC only today; an
        unsupported transport raises rather than mis-dispatching). Streaming is
        used when the card advertises ``capabilities.streaming`` unless
        ``prefer_stream`` overrides; a stream error falls back to sync. The
        sticky ``context_id`` is threaded unless an explicit one is passed.

        Pass ``task_id`` to **continue an existing task** (e.g. answering an
        ``input-required`` prompt): the message is sent with the same
        ``taskId`` + ``contextId`` per spec §3.4.3. Continuations always go
        over the sync transport (a short follow-up doesn't need streaming and
        the stream primitive doesn't thread ``taskId``)."""
        transport = await self.preferred_transport()
        if transport is not A2ATransport.JSONRPC:
            raise A2ADispatchError(
                f"{self.name} advertises {transport.value} transport, "
                "which this client does not yet support"
            )
        ctx = context_id or self._context_id
        want_stream = (
            prefer_stream if prefer_stream is not None
            else await self.supports_streaming()
        )
        if task_id:
            want_stream = False  # continuations go sync

        if want_stream:
            try:
                streamed = await dispatch_message_stream(
                    self.url,
                    headers=self._headers,
                    user_text=text,
                    context_id=ctx,
                    timeout=timeout,
                    progress_callback=progress_callback,
                    push_notification_url=push_notification_url,
                    push_notification_token=push_notification_token,
                )
                # Stream returns text only today; task_id/state enrichment from
                # the SSE events lands in PR-2 (lifecycle).
                return A2AResult(text=streamed, context_id=ctx)
            except A2ADispatchError as e:
                logger.warning(
                    f"[a2a] {self.name} stream failed ({e}); falling back to message/send"
                )

        params: dict = {
            "contextId": ctx,
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
            },
        }
        if task_id:
            # Continue an existing task. Spec places taskId/contextId on the
            # Message; ORBIS's wire (client + server) keys them at the params
            # level, consistent with how contextId is sent above.
            params["taskId"] = task_id
            params["message"]["taskId"] = task_id
            params["message"]["contextId"] = ctx
        result = await _post_jsonrpc(
            self.url, "message/send", params,
            headers=self._headers, timeout=timeout,
        )
        parsed = _parse_result_object(result, fallback_context_id=ctx)
        if not parsed.text and not parsed.input_required:
            raise A2ADispatchError(f"response from {self.url} had no text reply")
        return parsed

    async def continue_task(
        self, prior: A2AResult, text: str, *, timeout: float = 60.0,
    ) -> A2AResult:
        """Answer an ``input-required`` (or otherwise continue a task) by
        re-sending with the prior result's ``task_id`` + ``context_id``."""
        return await self.send(
            text,
            context_id=prior.context_id,
            task_id=prior.task_id,
            timeout=timeout,
        )

    async def get_task(
        self, task_id: str, *, history_length: int | None = None, timeout: float = 30.0,
    ) -> A2AResult:
        """`tasks/get` — fetch the current Task snapshot (poll a long task)."""
        params: dict = {"id": task_id}
        if history_length is not None:
            params["historyLength"] = history_length
        result = await _post_jsonrpc(
            self.url, "tasks/get", params, headers=self._headers, timeout=timeout,
        )
        return _parse_result_object(result, fallback_context_id=self._context_id)

    async def cancel_task(self, task_id: str, *, timeout: float = 30.0) -> A2AResult:
        """`tasks/cancel` — request cancellation of an in-flight task."""
        result = await _post_jsonrpc(
            self.url, "tasks/cancel", {"id": task_id},
            headers=self._headers, timeout=timeout,
        )
        return _parse_result_object(result, fallback_context_id=self._context_id)

    async def poll_until_terminal(
        self,
        task_id: str,
        *,
        interval: float = 2.0,
        max_wait: float = 120.0,
        timeout: float = 30.0,
    ) -> A2AResult:
        """Poll `tasks/get` until the task reaches a terminal state, pauses for
        input (``input-required``/``auth-required``), or ``max_wait`` elapses.
        For non-streaming long tasks where the delegate can't push back (the
        localhost reality). Returns the last observed ``A2AResult``; the caller
        checks ``is_terminal`` / ``input_required``."""
        deadline = time.monotonic() + max_wait
        last = await self.get_task(task_id, timeout=timeout)
        while not last.is_terminal and not last.input_required:
            if time.monotonic() >= deadline:
                logger.warning(
                    f"[a2a] {self.name} task {task_id} still {last.state!r} "
                    f"after {max_wait}s — giving up the poll"
                )
                break
            await asyncio.sleep(interval)
            last = await self.get_task(task_id, timeout=timeout)
        return last
