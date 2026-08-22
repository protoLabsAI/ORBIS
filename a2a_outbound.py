"""ORBIS outbound A2A client — wraps a2a-sdk's ``Client`` behind ORBIS's interface.

``a2a-sdk`` owns the transport: agent-card discovery, JSON-RPC/proto wire, auth
interceptors, retries, push-config registration. This thin adapter presents the
same surface ORBIS's delegates + orchestrate loop already use —
``A2AClient(url, ...).send(query) -> A2AResult`` — so those call sites change
only their import line.

ORBIS is the first Python *router* on ``a2a-sdk`` (protoAgent#453 is a leaf with
no outbound client; protoWorkstacean is TypeScript), so there's no fleet client
to mirror — the SDK ``Client`` IS the reference and this is just the
proto<->dataclass glue. The 1.1 client is proto-based
(``send_message(SendMessageRequest) -> AsyncIterator[StreamResponse]``); the
glue builds the request Message and reads text/state off the terminal Task.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, SendMessageRequest, Task, TaskState

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]

# Min gap between spoken progress lines, so a chatty agent's status_updates
# don't flood. We only narrate the agent's REAL streamed status text (the
# status_update message / tool-call frames it emits) — never a generic filler.
_PROGRESS_MIN_GAP = float(os.environ.get("A2A_PROGRESS_MIN_GAP_S", "5"))

_TERMINAL = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
}
_STATE_NAMES = {
    TaskState.TASK_STATE_SUBMITTED: "submitted",
    TaskState.TASK_STATE_WORKING: "working",
    TaskState.TASK_STATE_INPUT_REQUIRED: "input-required",
    TaskState.TASK_STATE_COMPLETED: "completed",
    TaskState.TASK_STATE_FAILED: "failed",
    TaskState.TASK_STATE_CANCELED: "canceled",
}


class A2ADispatchError(Exception):
    """Outbound A2A failure. The caller speaks the message back to the user."""


class A2ADispatchTimeout(A2ADispatchError):
    """The delegate didn't answer within the wall-clock bound. A distinct
    type so the dispatch chokepoint (``agent/delegates.py``) can count
    timeouts separately from hard failures (#683 Phase E); callers that
    catch ``A2ADispatchError`` are unaffected."""


@dataclass
class A2AResult:
    """Result of one outbound turn — preserves the old client's surface so
    delegates.py / orchestrate.py are unchanged."""

    text: str
    state: str | None = None
    task_id: str | None = None
    context_id: str | None = None
    input_required: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.state in ("completed", "failed", "canceled")


async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


def _origin_of(url: str) -> str:
    """scheme://host[:port] of a JSON-RPC url (where the agent card lives)."""
    from urllib.parse import urlparse

    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else url


def _part_text(part) -> str:
    try:
        return part.text or ""
    except Exception:  # noqa: BLE001
        return ""


def _task_answer_text(task: Task) -> str:
    """The agent's answer text. A2A servers place it differently:

    - ORBIS's own executor emits a terminal **artifact**.
    - The workstacean gateway (Ava) puts it on the task's **status message**
      and as the last agent turn in **history** — no artifacts.

    Check all three (last-non-empty wins) so we never speak an empty
    "<source> says —". Order: artifact → status.message → last agent history."""
    out = ""
    for art in task.artifacts:
        for p in art.parts:
            t = _part_text(p)
            if t:
                out = t
    if out:
        return out
    # Terminal status message (Ava / workstacean shape).
    out = _status_text(task)
    if out:
        return out
    # Last non-user message in history.
    try:
        for m in reversed(list(task.history)):
            if m.role == Role.ROLE_USER:
                continue
            t = "".join(_part_text(p) for p in m.parts)
            if t:
                return t
    except Exception:  # noqa: BLE001
        pass
    return out


def _status_message_text(status) -> str:
    """Text on a TaskStatus message — the terminal answer for buffer-then-answer
    agents (Ava), and where an input-required question lives."""
    try:
        if hasattr(status, "HasField") and not status.HasField("message"):
            return ""
        return "".join(_part_text(p) for p in status.message.parts)
    except Exception:  # noqa: BLE001
        return ""


def _status_text(task: Task) -> str:
    """Text on the task's status message (see _status_message_text)."""
    return _status_message_text(task.status)


async def _stamp_trace_headers(request: httpx.Request) -> None:
    """httpx request hook: attach the live Langfuse trace headers to every
    outbound A2A request, so a delegate that joins caller traces (protoAgent
    does) nests its work under ORBIS's turn.

    Per-request rather than baked into the client because the client is
    cached across turns while the trace changes every turn. Reading the
    active trace here is task-local (ContextVar-backed), so concurrent
    dispatches can't cross-stamp each other. No-ops (empty dict) when
    tracing is off or no trace is live — never fails a request."""
    try:
        from agent import tracing
        for k, v in tracing.propagation_headers(trace=tracing.active_trace()).items():
            request.headers[k] = v
    except Exception:  # noqa: BLE001 — observability must never break dispatch
        pass


class A2AClient:
    """Sticky-context outbound client for one delegate (wraps an SDK ``Client``)."""

    def __init__(
        self,
        url: str,
        *,
        headers: dict | None = None,
        card_origin: str | None = None,
        name: str = "a2a",
        context_id: str | None = None,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url
        self.headers = dict(headers or {})
        self.card_origin = card_origin
        self.name = name
        self._context_id = context_id or str(uuid.uuid4())
        self._client = None
        # An injected client (e.g. ASGITransport in tests, or a shared pool)
        # is reused as-is; otherwise we own one keyed to this delegate's auth.
        self._httpx: httpx.AsyncClient | None = httpx_client
        self._owns_httpx = httpx_client is None

    @property
    def context_id(self) -> str:
        return self._context_id

    async def supports_streaming(self) -> bool:
        # Opt into streaming; the SDK still gates on the card's
        # `capabilities.streaming` (base_client) and falls back to message/send
        # for non-streaming agents, so this is "willing to stream", negotiated
        # per-card. Streaming is client→server SSE (not push), so it keeps the
        # "never depend on a push callback to progress" property on localhost/
        # tailnet while giving real status_update heartbeats + clean termination.
        return True

    async def _ensure_client(self):
        if self._client is not None:
            return self._client
        if self._httpx is None:
            self._httpx = httpx.AsyncClient(
                headers=self.headers,
                timeout=60.0,
                event_hooks={"request": [_stamp_trace_headers]},
            )
        factory = ClientFactory(ClientConfig(httpx_client=self._httpx, streaming=True))
        # The card lives at the origin's /.well-known/agent-card.json, not under
        # the /a2a JSON-RPC path — discover from card_origin (delegate.origin()),
        # falling back to deriving the origin from the JSON-RPC url.
        discovery_url = self.card_origin or _origin_of(self.url)
        self._client = await _maybe_await(factory.create_from_url(discovery_url))
        return self._client

    async def send(
        self,
        query: str,
        *,
        context_id: str | None = None,
        task_id: str | None = None,
        prefer_stream: bool = False,
        timeout: float = 120.0,
        progress_callback: ProgressCallback | None = None,
        on_task: Callable[[str, str | None], None] | None = None,
        push_notification_url: str | None = None,
        push_notification_token: str | None = None,
        **_ignored,
    ) -> A2AResult:
        ctx = context_id or self._context_id
        self._context_id = ctx
        try:
            client = await self._ensure_client()
        except Exception as exc:  # noqa: BLE001
            raise A2ADispatchError(f"{self.name}: client init failed: {exc}") from exc

        msg = Message(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_USER,
            parts=[Part(text=query)],
            context_id=ctx,
        )
        if task_id:
            msg.task_id = task_id
        request = SendMessageRequest(message=msg)
        # Push-back registration (#695). These params were accepted-and-
        # ignored since the a2a-sdk migration (**_ignored swallowed them);
        # now they attach a TaskPushNotificationConfig so the remote POSTs
        # terminal updates to our /a2a/callback — which is how a result
        # survives a dispatch timeout: the task keeps running server-side,
        # completes later, and the push correlates by task id (#689).
        if push_notification_url:
            pc = request.configuration.task_push_notification_config
            pc.url = push_notification_url
            if push_notification_token:
                pc.token = push_notification_token

        # We may get either shape, negotiated per-card by the SDK:
        #  - streaming: task(submitted) → status_update(working)* →
        #    artifact_update(answer) → status_update(completed)  [no final task]
        #  - non-streaming: a single terminal task snapshot
        # Capture the answer + terminal state from whichever arrives, and emit
        # one grounded "still working" beat off a real WORKING heartbeat.
        final_task: Task | None = None
        artifact_text = ""
        status_text = ""
        message_text = ""
        final_state = None
        resolved_task_id = task_id
        resolved_ctx = ctx
        last_progress_at = 0.0
        last_progress = ""
        task_seen = False

        def _notify_task() -> None:
            # Fire on_task ONCE, at the first event carrying a task id —
            # that's ~tens of ms in, so the caller's durable handle exists
            # long before a timeout, barge-in, or crash could lose the work.
            nonlocal task_seen
            if task_seen or on_task is None or not resolved_task_id:
                return
            task_seen = True
            try:
                on_task(resolved_task_id, resolved_ctx)
            except Exception:  # noqa: BLE001 — recording must never break a turn
                logger.warning(f"[a2a/{self.name}] on_task hook failed", exc_info=True)

        async def _consume() -> None:
            nonlocal final_task, artifact_text, status_text, message_text
            nonlocal final_state, resolved_task_id, resolved_ctx
            nonlocal last_progress_at, last_progress
            ev_count = 0
            async for resp in client.send_message(request):
                which = resp.WhichOneof("payload") if hasattr(resp, "WhichOneof") else None
                ev_count += 1
                if which == "task" or (which is None and resp.HasField("task")):
                    final_task = resp.task
                    final_state = resp.task.status.state
                    logger.info(
                        f"[a2a/{self.name}] ←#{ev_count} task state={_STATE_NAMES.get(resp.task.status.state, resp.task.status.state)}"
                    )
                    if resp.task.id:
                        resolved_task_id = resp.task.id
                    if resp.task.context_id:
                        resolved_ctx = resp.task.context_id
                    _notify_task()
                elif which == "status_update":
                    su = resp.status_update
                    final_state = su.status.state
                    if su.task_id:
                        resolved_task_id = su.task_id
                    if su.context_id:
                        resolved_ctx = su.context_id
                    _notify_task()
                    msg_text = _status_message_text(su.status)
                    logger.info(
                        f"[a2a/{self.name}] ←#{ev_count} status_update "
                        f"state={_STATE_NAMES.get(su.status.state, su.status.state)} "
                        f"text={(msg_text or '')[:90]!r}"
                    )
                    if su.status.state == TaskState.TASK_STATE_WORKING:
                        # Narrate the agent's REAL streamed progress — the text it
                        # puts on an intermediate status message (and, when the
                        # fleet emits it, its tool-call-v1 frames). NOT a generic
                        # "still working" filler: if a heartbeat carries no text
                        # we stay quiet. Throttled so a chatty agent can't flood.
                        now = time.monotonic()
                        if (
                            progress_callback is not None
                            and msg_text
                            and msg_text != last_progress
                            and now - last_progress_at >= _PROGRESS_MIN_GAP
                        ):
                            last_progress_at = now
                            last_progress = msg_text
                            try:
                                await progress_callback(msg_text)
                            except Exception:  # noqa: BLE001
                                pass
                    elif msg_text:
                        status_text = msg_text  # terminal / input-required text
                elif which == "artifact_update":
                    t = "".join(
                        _part_text(p) for p in resp.artifact_update.artifact.parts
                    )
                    logger.info(f"[a2a/{self.name}] ←#{ev_count} artifact_update +{len(t)} chars")
                    if t:
                        artifact_text = t
                elif which == "message" or (which is None and resp.HasField("message")):
                    for p in resp.message.parts:
                        message_text += _part_text(p)

        # Enforce the wall-clock bound. A buffer-then-answer gateway (Ava routes
        # through the fleet) can hold the response; without this the await could
        # wedge the caller — which, on the voice loop, looked like ORBIS "going
        # silent" mid-turn. The async/orchestrate paths run this in the
        # background, so the cap just stops a runaway task, never the voice loop.
        try:
            await asyncio.wait_for(_consume(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise A2ADispatchTimeout(
                f"{self.name}: no response within {timeout:.0f}s"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise A2ADispatchError(f"{self.name}: send failed: {exc}") from exc

        # Latest state seen across ALL events wins — in the streaming shape the
        # initial `task` is `submitted`, and the terminal state arrives later as
        # a `status_update(completed)`. Don't let the stale submitted task win.
        state = final_state if final_state is not None else TaskState.TASK_STATE_COMPLETED
        input_required = state == TaskState.TASK_STATE_INPUT_REQUIRED
        text = (
            (_task_answer_text(final_task) if final_task is not None else "")
            or artifact_text
            or status_text
            or message_text
        )
        return A2AResult(
            text=text,
            state=_STATE_NAMES.get(state),
            task_id=(
                final_task.id if final_task is not None and final_task.id
                else resolved_task_id
            ) or None,
            context_id=(
                final_task.context_id if final_task is not None and final_task.context_id
                else resolved_ctx
            ),
            input_required=input_required,
        )

    async def cancel(self, task_id: str, *, timeout: float = 15.0) -> str | None:
        """Cancel a previously dispatched task (A2A ``tasks/cancel``) — the
        wire half of verbal cancel (#681, "stop that" → the delegated work
        actually stops). Returns the remote's post-cancel state name (may be
        ``canceled``, or a terminal state if it finished first). Raises
        ``A2ADispatchError`` on failure — the caller decides what that means
        (ORBIS still marks its local handle canceled: the USER's intent is
        authoritative for what gets delivered later)."""
        from a2a.types import CancelTaskRequest

        try:
            client = await self._ensure_client()
        except Exception as exc:  # noqa: BLE001
            raise A2ADispatchError(f"{self.name}: client init failed: {exc}") from exc
        try:
            task = await asyncio.wait_for(
                _maybe_await(client.cancel_task(CancelTaskRequest(id=task_id))),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise A2ADispatchTimeout(
                f"{self.name}: tasks/cancel timed out after {timeout:.0f}s"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise A2ADispatchError(f"{self.name}: tasks/cancel failed: {exc}") from exc
        state = getattr(getattr(task, "status", None), "state", None)
        name = _STATE_NAMES.get(state) if state is not None else None
        logger.info(f"[a2a/{self.name}] cancelled task {task_id} → {name}")
        return name

    async def get_task(self, task_id: str, *, timeout: float = 15.0) -> A2AResult:
        """Requery a previously dispatched task by id (A2A ``tasks/get``).

        The reconnect/restart requery of the durable outbound-task registry
        (#678 Phase B) uses this to recover results that completed while no
        session was live. Raises ``A2ADispatchError`` on any failure — the
        caller decides whether that means retry-later or expire."""
        from a2a.types import GetTaskRequest

        try:
            client = await self._ensure_client()
        except Exception as exc:  # noqa: BLE001
            raise A2ADispatchError(f"{self.name}: client init failed: {exc}") from exc
        try:
            task = await asyncio.wait_for(
                _maybe_await(client.get_task(GetTaskRequest(id=task_id))),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise A2ADispatchTimeout(
                f"{self.name}: tasks/get timed out after {timeout:.0f}s"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise A2ADispatchError(f"{self.name}: tasks/get failed: {exc}") from exc
        state = task.status.state
        return A2AResult(
            text=_task_answer_text(task),
            state=_STATE_NAMES.get(state),
            task_id=task.id or task_id,
            context_id=task.context_id or None,
            input_required=state == TaskState.TASK_STATE_INPUT_REQUIRED,
        )

    async def close(self) -> None:
        if self._httpx is not None and self._owns_httpx:
            await self._httpx.aclose()
        self._httpx = None
        self._client = None
