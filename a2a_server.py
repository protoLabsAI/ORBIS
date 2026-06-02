"""ORBIS A2A 1.0 server wiring — mounts /a2a on a2a-sdk + protolabs-a2a.

Replaces the hand-rolled ``a2a/server.py``. The SDK owns every piece of protocol
mechanics (JSON-RPC dispatch, SSE streaming, the task lifecycle, push delivery);
``protolabs_a2a`` builds the agent card (declaring the 4 fleet extensions) + the
auth schemes; ``a2a_executor`` bridges ORBIS's inbound ReAct brain.

Mirrors protoAgent#453's server wiring (auth / executor / durable stores +
SSRF-guarded push delivery). The SDK's ``push_sender`` delivers task updates to
callers who register a ``pushNotificationConfig``. ORBIS additionally keeps an
inbound ``/a2a/callback`` receiver: when ORBIS dispatches OUT and a delegate
pushes results BACK (live on the tailnet), the receiver routes them into the
active session's DeliveryController (or stashes for the next connect).
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
import protolabs_a2a as pa
from fastapi import Request
from fastapi.responses import JSONResponse
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.types import AgentSkill

import a2a_auth
from a2a_executor import OrbisAgentExecutor, set_terminal_hook
from a2a_stores import build_a2a_stores, build_push_sender, initialize_a2a_stores

logger = logging.getLogger(__name__)

AGENT_NAME = os.environ.get("A2A_AGENT_NAME", "orbis")
A2A_AUTH_TOKEN = os.environ.get("A2A_AUTH_TOKEN", "")

_SKILLS = [
    AgentSkill(
        id="chat",
        name="Chat",
        description=(
            "Free-form conversation with ORBIS and delegation to configured "
            "A2A / OpenAI-compatible / ACP coding agents."
        ),
        tags=["voice", "chat", "delegation"],
        examples=[
            "summarize the current project state",
            "ask my coding agent to inspect the failing test",
        ],
    )
]


def register_a2a_routes(
    app,
    *,
    text_stream_factory,
    version: str = "0",
    host: str | None = None,
    terminal_hook=None,
    delivery_provider=None,
    user_id_provider=None,
    **_ignored,
) -> None:
    """Mount the A2A 1.0 endpoint: ``/a2a`` (JSON-RPC) + the agent card at
    ``/.well-known/agent-card.json`` (declaring the 4 protoLabs extensions) +
    the ``/a2a/callback`` inbound push-back receiver.

    ``text_stream_factory(text, context_id, *, resume, caller_trace)`` is the
    inbound producer-event async generator (built in app.py).
    ``delivery_provider()`` / ``user_id_provider()`` resolve the active session's
    DeliveryController + owner for routing delegate push-backs.
    """
    base_url = f"http://{host or f'{AGENT_NAME}:7866'}/a2a"
    card = pa.build_agent_card(
        name=AGENT_NAME,
        description=(
            "ORBIS — native desktop voice companion and router for the user's "
            "configured agents. Inbound A2A returns text + cost/tool-call "
            "telemetry; interactive voice runs in the local Tauri desktop app."
        ),
        url=base_url,
        version=str(version),
        skills=_SKILLS,
        bearer=bool(A2A_AUTH_TOKEN),
    )

    # The SDK advertises security schemes on the card but does NOT enforce them
    # — this middleware does (ORBIS-strict closed-by-default).
    a2a_auth.install(
        app,
        bearer_token=A2A_AUTH_TOKEN,
        api_key=os.environ.get("ORBIS_API_KEY", ""),
        allowed_origins_raw=os.environ.get("A2A_ALLOWED_ORIGINS", ""),
    )

    if terminal_hook is not None:
        set_terminal_hook(terminal_hook)

    # Durable SQLite task + push-config stores (survive restart; 24h task TTL).
    # Push-config store rejects SSRF callback URLs at set-time; the sender
    # re-validates at send-time.
    task_store, push_config_store, task_db, push_db = build_a2a_stores()
    # Force schema init + TTL sweep when we're at module-import time (no running
    # loop — the normal app.py path). If a loop is already running (tests, async
    # callers), skip it — the SDK DB stores lazy-init their schema on first use.
    try:
        asyncio.get_running_loop()
        logger.info("[a2a] event loop running — stores lazy-init on first use")
    except RuntimeError:
        asyncio.run(initialize_a2a_stores(task_store, push_config_store))
    logger.info("[a2a] durable stores ready (tasks=%s, push=%s)", task_db, push_db)

    push_client = httpx.AsyncClient(timeout=30)
    handler = DefaultRequestHandler(
        agent_executor=OrbisAgentExecutor(text_stream_factory),
        task_store=task_store,
        agent_card=card,
        push_config_store=push_config_store,
        push_sender=build_push_sender(push_config_store, push_client),
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a"),
    )

    # Inbound push-back receiver: a delegate ORBIS dispatched to (async, on the
    # tailnet) POSTs its result here. Route it into the live session's
    # DeliveryController, or stash for the next connect. (The SDK has no inbound
    # receiver — this is ORBIS's, carried over from the hand-rolled handler.)
    @app.post("/a2a/callback")
    async def _a2a_callback(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid json"})
        text = _extract_text_from_any(body)
        caller = body.get("from") or body.get("agent") or "unknown"
        logger.info("[a2a/callback] from=%s text_len=%d", caller, len(text))
        if not text:
            return {"ok": True, "delivered": False, "reason": "no text"}
        delivery = delivery_provider() if delivery_provider else None
        if delivery is None:
            from agent.session_store import stash_delivery
            uid = user_id_provider() if user_id_provider else "default"
            stash_delivery(uid, {
                "phrase": f"{caller} says — {text}",
                "policy": "next_silence",
                "priority": "time_sensitive",
                "keywords": [],
            })
            logger.info("[a2a/callback] no active session — stashed under %r", uid)
            return {"ok": True, "delivered": False, "stashed": True}
        from agent.delivery import Priority
        await delivery.deliver(text, priority=Priority.TIME_SENSITIVE, source=caller)
        return {"ok": True, "delivered": True}

    logger.info(
        "[a2a] a2a-sdk 1.0 routes mounted (JSON-RPC /a2a, card "
        "/.well-known/agent-card.json, 4 extensions declared, push-back receiver)"
    )


def _extract_text_from_any(body: dict) -> str:
    """Hunt for text in a few common shapes (A2A task result, plain message, or
    nested envelope) — permissive so push-backs work across fleet versions."""
    if isinstance(body.get("text"), str):
        return body["text"]
    result = body.get("result") or body
    artifacts = result.get("artifacts") or []
    for art in artifacts:
        for part in art.get("parts") or []:
            if (part.get("kind") or part.get("type")) == "text" and part.get("text"):
                return part["text"]
    msg = body.get("message") or (result.get("message") if isinstance(result, dict) else None)
    if isinstance(msg, dict):
        for part in msg.get("parts") or []:
            if (part.get("kind") or part.get("type")) == "text" and part.get("text"):
                return part["text"]
    return ""
