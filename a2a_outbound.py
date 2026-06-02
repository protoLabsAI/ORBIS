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

import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, SendMessageRequest, Task, TaskState

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]

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
    """The agent's answer = the last non-empty text part across the task's
    artifacts (matches the executor's terminal-artifact ordering)."""
    out = ""
    for art in task.artifacts:
        for p in art.parts:
            t = _part_text(p)
            if t:
                out = t
    return out


def _status_text(task: Task) -> str:
    """Text on the task's status message (where an input-required question
    lives)."""
    try:
        msg = task.status.update  # StatusUpdate carries the agent message
        return "".join(_part_text(p) for p in msg.parts)
    except Exception:  # noqa: BLE001
        return ""


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
        # We drive non-streaming (poll/hold) for robustness on localhost/tailnet,
        # matching the orchestrate loop's "never depend on push to progress".
        return False

    async def _ensure_client(self):
        if self._client is not None:
            return self._client
        if self._httpx is None:
            self._httpx = httpx.AsyncClient(headers=self.headers, timeout=60.0)
        factory = ClientFactory(ClientConfig(httpx_client=self._httpx, streaming=False))
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

        final_task: Task | None = None
        message_text = ""
        try:
            async for resp in client.send_message(request):
                which = resp.WhichOneof("payload") if hasattr(resp, "WhichOneof") else None
                if which == "task" or resp.HasField("task"):
                    final_task = resp.task
                elif which == "message" or (which is None and resp.HasField("message")):
                    for p in resp.message.parts:
                        message_text += _part_text(p)
                # status_update / artifact_update: progress; final state comes
                # from the terminal task below. (Narration via progress_callback
                # is a follow-up — the orchestrate loop already narrates per step.)
        except Exception as exc:  # noqa: BLE001
            raise A2ADispatchError(f"{self.name}: send failed: {exc}") from exc

        if final_task is not None:
            state = final_task.status.state
            input_required = state == TaskState.TASK_STATE_INPUT_REQUIRED
            text = _task_answer_text(final_task) or message_text
            if input_required and not text:
                text = _status_text(final_task)
            return A2AResult(
                text=text,
                state=_STATE_NAMES.get(state),
                task_id=final_task.id or None,
                context_id=final_task.context_id or ctx,
                input_required=input_required,
            )
        return A2AResult(text=message_text, state="completed", context_id=ctx)

    async def close(self) -> None:
        if self._httpx is not None and self._owns_httpx:
            await self._httpx.aclose()
        self._httpx = None
        self._client = None
