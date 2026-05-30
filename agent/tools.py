"""Tool registry for the ORBIS voice agent.

ORBIS's tool surface is deliberately narrow:

  - ``delegate_to(target, query)`` — hand off to a user-configured agent
    (A2A or OpenAI-compat), blocking for the reply you relay in the same
    breath. The user's configured agents are where heavy reasoning lives;
    ORBIS is the voice frontend for them.
  - ``delegate_async(target, query)`` — fire-and-forget variant for longer
    work: acks immediately, dispatches in the background, and speaks the
    answer via the DeliveryController when the delegate finishes. Only
    registered when a DeliveryController is available.
  - ``adjust_personality`` — shift a personality axis in response to an
    explicit user request ("be more playful", "be less sarcastic").

Orb visual control (variant, palette, params, presets) is handled by
other processes outside the LLM tool surface.

Nothing else ships: no calculator, no search, no datetime, no
fetch_url — those all become user-configured delegates if actually
needed. Tools that would create support burden without being the
product's differentiator are excluded by design.

Registration: tools self-register via ``@tool(...)`` at import time.
``delegate_to`` is hand-wired because its JSON schema enumerates the
live delegate registry and therefore changes per-session.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams, LLMService

from .delegates import DelegateError, DelegateRegistry, dispatch as delegate_dispatch
from .delivery import DeliveryController, Priority
from .filler import Latency

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Markdown → speech (orbis-nhu). Delegate replies (e.g. Ava) come back as
# markdown; spoken verbatim by TTS that's "asterisk asterisk number 4028,
# open bracket…". Flatten the unambiguous constructs before the text reaches
# the voice path. Conservative on purpose: the italic rule requires the
# delimiter to sit on a word boundary so "snake_case" / "2 * 3" are left
# alone.
# ---------------------------------------------------------------------------
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")            # [label](url) → label
_MD_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)     # **x** / __x__ → x
_MD_ITALIC = re.compile(
    r"(?<![A-Za-z0-9])([*_])(?=\S)(.+?)(?<=\S)\1(?![A-Za-z0-9])", re.DOTALL
)                                                          # *x* / _x_ → x
_MD_CODE = re.compile(r"`+([^`]+)`+")                      # `x` → x
_MD_HEADER = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]*")    # ## h → h
_MD_BULLET = re.compile(r"(?m)^[ \t]*[-*+][ \t]+")         # - item → item
_MD_QUOTE = re.compile(r"(?m)^[ \t]*>[ \t]?")              # > q → q


def _strip_markdown_for_speech(text: str) -> str:
    if not text:
        return text
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_BOLD.sub(r"\2", text)
    text = _MD_ITALIC.sub(r"\2", text)
    text = _MD_CODE.sub(r"\1", text)
    text = _MD_HEADER.sub("", text)
    text = _MD_BULLET.sub("", text)
    text = _MD_QUOTE.sub("", text)
    return text.strip()


# Hand-wired tools (built per-session, not via @tool) that should still be
# treated as async — they ack immediately and surface their real result
# later via the DeliveryController, so the progress-narration loop must NOT
# run for them. ``_AsyncToolNames`` checks this set in addition to the
# @tool registry.
_HANDWIRED_ASYNC_NAMES = frozenset({"delegate_async"})

# Strong refs to in-flight background delegation tasks so the event loop
# can't GC them mid-flight (asyncio holds only weak refs to bare tasks).
_BG_DELEGATE_TASKS: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Tool registry — @tool() decorator writes here at import time.
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, dict[str, Any]]  # JSON-schema properties
    required: list[str]
    handler: Callable                       # async (params) -> None
    latency: Latency = Latency.MEDIUM
    async_tool: bool = False                # True → cancel_on_interruption=False


_TOOL_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    name: str,
    description: str,
    *,
    parameters: dict[str, dict[str, Any]] | None = None,
    required: list[str] | None = None,
    latency: Latency = Latency.MEDIUM,
    async_tool: bool = False,
):
    """Decorator — registers an async handler as a tool at import time.

    Every field from the decorator flows into the ToolSpec; no hardcoded
    latency dict or hand-wired LLM registration needed afterwards.
    """

    def decorator(handler: Callable):
        if name in _TOOL_REGISTRY:
            logger.warning(f"[tools] {name}: duplicate registration, overwriting")
        _TOOL_REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            parameters=parameters or {},
            required=required or [],
            handler=handler,
            latency=latency,
            async_tool=async_tool,
        )
        return handler

    return decorator


def latency_for(tool_name: str) -> Latency:
    """Expected latency for a tool — reads the registry. Unknown tools
    default to MEDIUM. ``delegate_to`` isn't in the registry (hand-wired)
    so it also falls back to MEDIUM."""
    spec = _TOOL_REGISTRY.get(tool_name)
    return spec.latency if spec else Latency.MEDIUM


class _AsyncToolNames:
    """Derived view on the registry for `name in ASYNC_TOOL_NAMES` checks."""
    def __contains__(self, name: str) -> bool:
        if name in _HANDWIRED_ASYNC_NAMES:
            return True
        spec = _TOOL_REGISTRY.get(name)
        return bool(spec and spec.async_tool)

    def __iter__(self):
        seen = set(_HANDWIRED_ASYNC_NAMES)
        for name in _HANDWIRED_ASYNC_NAMES:
            yield name
        for name, spec in _TOOL_REGISTRY.items():
            if spec.async_tool and name not in seen:
                yield name


ASYNC_TOOL_NAMES = _AsyncToolNames()


def _schema_for(spec: ToolSpec) -> FunctionSchema:
    return FunctionSchema(
        name=spec.name,
        description=spec.description,
        properties=spec.parameters,
        required=spec.required,
    )


@tool(
    "adjust_personality",
    (
        "Apply a small, explicit personality shift in response to the user "
        "asking you to ('be more playful', 'be less sarcastic', 'be warmer'). "
        "Use SPARINGLY — only when the user directs a change. Deltas are "
        "small by design; the persistent personality drifts naturally over "
        "many sessions and shouldn't lurch. Axis slugs: playful_serious, "
        "warm_guarded, sarcastic_sincere, verbose_terse, hopeful_cynical, "
        "grandiose_grounded, probing_incurious, philosophical_pragmatic, "
        "independent_clingy, curious_bored. Positive delta shifts toward "
        "the second adjective; negative toward the first."
    ),
    parameters={
        "axis": {
            "type": "string",
            "description": "Axis slug (see list above)",
        },
        "delta": {
            "type": "number",
            "description": "Shift in [-0.2, +0.2]. Typical is 0.1.",
        },
    },
    required=["axis", "delta"],
    latency=Latency.FAST,
)
async def adjust_personality_handler(params: FunctionCallParams) -> None:
    axis = (params.arguments.get("axis") or "").strip()
    try:
        delta = float(params.arguments.get("delta", 0.0))
    except (TypeError, ValueError):
        delta = 0.0
    if not axis or abs(delta) < 0.01:
        await params.result_callback("I didn't catch a clear personality axis + delta.")
        return
    # Clamp to the directable-range; the DAL clamps again at |0.3|.
    delta = max(-0.2, min(0.2, delta))

    # Import here to avoid module-load cycles.
    try:
        from app import get_memory  # type: ignore
        get_memory().personality.drift(axis, delta, reason="user directive")
    except Exception as exc:
        logger.info(f"[orb] adjust_personality memory write failed: {exc}")
        # Fall through — still confirm verbally so the user hears the intent.

    direction = "more" if delta > 0 else "less"
    logger.info(f"[personality] adjust_personality axis={axis} delta={delta:+.2f}")
    await params.result_callback(f"Okay — dialing {axis} {direction}.")


@tool(
    "check_inbox",
    (
        "Check the agent's inbox for messages pushed in by external "
        "systems (webhooks, cron, sister agents). Use when the user asks "
        "'anything new?' / 'any messages?' / 'what's in my inbox?', or "
        "when picking up a conversation that might have offline updates. "
        "\n\n"
        "Messages have a priority — 'now' is urgent, 'next' is the "
        "default, 'later' is background chatter. The default check "
        "returns 'now' + 'next' (skipping 'later') so quick checks aren't "
        "drowned in noise. Pass priority_floor='later' to also drain "
        "background messages — use when the user explicitly wants 'show "
        "me everything' or 'full inbox'. 'now'-priority items are also "
        "auto-surfaced at session start so you may already know about "
        "them; calling check_inbox after that just confirms / re-shows."
        "\n\n"
        "Surfaced messages are marked delivered so they don't show up "
        "again on the next call."
    ),
    parameters={
        "priority_floor": {
            "type": "string",
            "enum": ["now", "next", "later"],
            "description": (
                "Lowest priority to include. 'now'=urgent only, "
                "'next'=urgent+normal (default), 'later'=everything."
            ),
        },
        "include_delivered": {
            "type": "boolean",
            "description": "Include already-read messages too. Default false.",
        },
    },
    required=[],
    latency=Latency.FAST,
)
async def check_inbox_handler(params: FunctionCallParams) -> None:
    raw_floor = (params.arguments.get("priority_floor") or "next").strip().lower()
    if raw_floor not in ("now", "next", "later"):
        raw_floor = "next"
    include_delivered = bool(params.arguments.get("include_delivered", False))

    try:
        from app import get_memory  # type: ignore
        mem = get_memory()
        if include_delivered:
            msgs = mem.inbox.list_all(limit=20)
        else:
            msgs = mem.inbox.list_unread(
                priority_floor=raw_floor,  # type: ignore[arg-type]
                limit=20,
            )
            # Mark surfaced messages delivered so the next check doesn't
            # re-read them. Doing this BEFORE returning means a crash on
            # the result callback won't replay the same batch — the user
            # would otherwise hear the same message twice.
            ids = [m["id"] for m in msgs]
            if ids:
                mem.inbox.mark_delivered(ids)
    except Exception as exc:
        logger.info(f"[inbox] check_inbox failed: {exc}")
        await params.result_callback(
            "I couldn't reach the inbox right now."
        )
        return

    if not msgs:
        await params.result_callback(
            "Inbox is empty." if include_delivered else "No new messages."
        )
        return

    # Compact summary the LLM can riff on. The full body is in the
    # tool result so the model can quote it back if asked, but the
    # spoken summary stays to one line per message.
    lines = [f"You have {len(msgs)} message{'s' if len(msgs) != 1 else ''}:"]
    for m in msgs:
        sender = m.get("sender") or "unknown"
        subject = m.get("subject") or ""
        body = m.get("body") or ""
        priority = m.get("priority") or "next"
        snippet = body[:200] + ("…" if len(body) > 200 else "")
        prefix = "[urgent] " if priority == "now" else ""
        lines.append(f"- {prefix}from {sender}: {subject}\n  {snippet}")
    await params.result_callback("\n".join(lines))


# ---------------------------------------------------------------------------
# delegate_to — hand-wired because its schema is dynamic per-session
# (derived from the live DelegateRegistry).
# ---------------------------------------------------------------------------

def _delegate_to_schema(registry: DelegateRegistry) -> FunctionSchema:
    """Built dynamically — `target` is enum-restricted to known delegates,
    and the description enumerates what each delegate is good for so the
    LLM can pick correctly."""
    items = registry.all()
    target_lines = "\n".join(f"  - {d.name}: {d.description}" for d in items)
    return FunctionSchema(
        name="delegate_to",
        description=(
            "Hand off a question to one of the user's configured agents. "
            "Use for genuine depth, current information, coding tasks, "
            "research, or anything that isn't quick small talk. The "
            "delegate's reply streams back and you relay/summarise it.\n\n"
            f"Available targets:\n{target_lines}\n\n"
            "Pass `target` (one of the names above) and `query` (the "
            "question, phrased as you'd ask a person)."
        ),
        properties={
            "target": {
                "type": "string",
                "enum": [d.name for d in items],
                "description": "Which delegate to ask",
            },
            "query": {
                "type": "string",
                "description": "The question to ask",
            },
        },
        required=["target", "query"],
    )


def _delegate_to_handler(
    registry: DelegateRegistry,
    *,
    delivery: "DeliveryController | None" = None,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
):
    async def _handler(params: FunctionCallParams) -> None:
        target = (params.arguments.get("target") or "").strip()
        query = (params.arguments.get("query") or "").strip()
        if not target or not query:
            await params.result_callback(
                "I need both a target and a question to delegate."
            )
            return
        delegate = registry.get(target)
        if not delegate:
            available = ", ".join(registry.names()) or "(none)"
            await params.result_callback(
                f"I don't know a delegate named '{target}'. Available: {available}."
            )
            return
        logger.info(f"[delegate_to] target={target} type={delegate.type} query={query!r}")

        # Stream progress narration back through the voice pipeline when
        # available. Only wired for A2A delegates (OpenAI delegates don't
        # stream status updates the same way).
        progress_cb = None
        if delivery is not None and delegate.type == "a2a":
            async def _progress(msg: str) -> None:
                await delivery.speak_now(msg, source=target)
            progress_cb = _progress

        try:
            result = await delegate_dispatch(
                delegate, query,
                progress_callback=progress_cb,
                push_notification_url=push_notification_url,
                push_notification_token=push_notification_token,
            )
            await params.result_callback(_strip_markdown_for_speech(result))
        except DelegateError as e:
            await params.result_callback(f"Couldn't reach {target}: {e}")
        except Exception as e:
            logger.exception(f"[delegate_to] unexpected error: {e}")
            await params.result_callback(f"Delegation to {target} errored: {e}")

    return _handler


# ---------------------------------------------------------------------------
# delegate_async — fire-and-forget delegation (orbis-1s0). Returns an
# immediate verbal ack so the conversation keeps flowing, then surfaces the
# delegate's answer through the DeliveryController when it lands. Use for
# work that takes more than a few seconds; `delegate_to` stays for quick
# questions whose answer you relay in the same breath.
# ---------------------------------------------------------------------------

def _delegate_async_schema(registry: DelegateRegistry) -> FunctionSchema:
    items = registry.all()
    target_lines = "\n".join(f"  - {d.name}: {d.description}" for d in items)
    return FunctionSchema(
        name="delegate_async",
        description=(
            "Hand off a task to one of the user's agents in the BACKGROUND. "
            "Use this instead of `delegate_to` when the task will take more "
            "than a few seconds (research, a multi-step job, 'go do X and "
            "report back'). This returns immediately — acknowledge to the "
            "user that you've sent it off and will tell them when it's done; "
            "do NOT wait for or invent the answer. The real reply is spoken "
            "automatically when the agent finishes.\n\n"
            f"Available targets:\n{target_lines}\n\n"
            "Pass `target` (one of the names above) and `query`."
        ),
        properties={
            "target": {
                "type": "string",
                "enum": [d.name for d in items],
                "description": "Which delegate to ask",
            },
            "query": {
                "type": "string",
                "description": "The task to hand off, phrased as you'd ask a person",
            },
        },
        required=["target", "query"],
    )


def _delegate_async_handler(
    registry: DelegateRegistry,
    *,
    delivery: DeliveryController,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
):
    """Async tool: ack now, dispatch in the background, deliver the result
    through the DeliveryController (TIME_SENSITIVE → spoken at the next
    natural pause) when the delegate finishes."""
    bg_timeout = float(os.environ.get("DELEGATE_ASYNC_TIMEOUT", "300"))

    async def _handler(params: FunctionCallParams) -> None:
        target = (params.arguments.get("target") or "").strip()
        query = (params.arguments.get("query") or "").strip()
        if not target or not query:
            await params.result_callback(
                "I need both a target and a task to hand off."
            )
            return
        delegate = registry.get(target)
        if not delegate:
            available = ", ".join(registry.names()) or "(none)"
            await params.result_callback(
                f"I don't know a delegate named '{target}'. Available: {available}."
            )
            return

        logger.info(f"[delegate_async] target={target} query={query!r} (background)")

        async def _run_and_deliver() -> None:
            try:
                result = await delegate_dispatch(
                    delegate, query,
                    timeout=bg_timeout,
                    push_notification_url=push_notification_url,
                    push_notification_token=push_notification_token,
                )
                await delivery.deliver(
                    _strip_markdown_for_speech(result),
                    priority=Priority.TIME_SENSITIVE, source=target,
                )
            except DelegateError as e:
                logger.warning(f"[delegate_async] {target} failed: {e}")
                await delivery.deliver(
                    f"that thing I handed off didn't go through — {e}",
                    priority=Priority.TIME_SENSITIVE, source=target,
                )
            except Exception as e:
                logger.exception(f"[delegate_async] {target} errored: {e}")
                await delivery.deliver(
                    f"that thing I handed off to {target} errored out.",
                    priority=Priority.TIME_SENSITIVE, source=target,
                )

        task = asyncio.create_task(_run_and_deliver())
        _BG_DELEGATE_TASKS.add(task)
        task.add_done_callback(_BG_DELEGATE_TASKS.discard)

        # Immediate ack — the LLM speaks this and the turn ends; the answer
        # arrives later via the DeliveryController.
        await params.result_callback(
            f"Sent that off to {target}. I'll let you know as soon as they're back."
        )

    return _handler


# ---------------------------------------------------------------------------
# Text-mode tool runner (A2A inbound ReAct) — unchanged interface.
# ---------------------------------------------------------------------------

async def run_text_tool(
    name: str,
    arguments: dict,
    *,
    delegates: DelegateRegistry | None = None,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
) -> str:
    """Invoke a tool handler in text mode (no pipecat FunctionCallParams).

    Returns the string result the handler would have passed to
    result_callback. Used by the inbound A2A ReAct loop so external
    agents can drive the same tool registry the voice path uses.

    Async tools are NOT exposed here — they require a live voice
    session to narrate back on completion.
    """
    class _P:  # duck-typed FunctionCallParams stand-in
        def __init__(self, args: dict) -> None:
            self.arguments = args
            self._out: str = ""
        async def result_callback(self, text: Any) -> None:
            self._out = "" if text is None else str(text)
    params = _P(arguments)

    spec = _TOOL_REGISTRY.get(name)
    if spec and not spec.async_tool:
        await spec.handler(params)
        return params._out

    if name == "delegate_to" and delegates is not None:
        handler = _delegate_to_handler(
            delegates,
            delivery=None,                      # no voice session; skip progress
            push_notification_url=push_notification_url,
            push_notification_token=push_notification_token,
        )
        await handler(params)
        return params._out

    return f"(unknown or unavailable tool: {name})"


def build_text_tool_schemas(delegates: DelegateRegistry | None = None) -> list[dict]:
    """Build the OpenAI tools-parameter list for the text-mode ReAct
    loop. Mirrors the schemas register_tools registers with pipecat,
    minus any async-only tools."""
    schemas: list[FunctionSchema] = [
        _schema_for(spec)
        for spec in _TOOL_REGISTRY.values()
        if not spec.async_tool
    ]
    if delegates is not None and delegates.names():
        schemas.append(_delegate_to_schema(delegates))
    return [{"type": "function", "function": s.to_default_dict()} for s in schemas]


# ---------------------------------------------------------------------------
# Registration — iterates the registry for decorated tools, hand-wires
# delegate_to (dynamic schema).
# ---------------------------------------------------------------------------

def register_tools(
    llm: LLMService,
    *,
    on_finish=None,
    delivery: DeliveryController | None = None,
    delegates: DelegateRegistry | None = None,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
) -> ToolsSchema:
    """Attach handlers + return the schema for the LLMContext.

    Decorated tools (``@tool``) are registered automatically.
    ``delegate_to`` is hand-wired because it closes over the live
    DelegateRegistry and builds its schema per-session.
    """

    def _wrap_sync(handler):
        async def _wrapped(params: FunctionCallParams) -> None:
            try:
                await handler(params)
            finally:
                if on_finish is not None:
                    try:
                        on_finish()
                    except Exception as e:
                        logger.warning(f"on_finish hook raised: {e}")
        _wrapped.__name__ = getattr(handler, "__name__", "_wrapped")
        return _wrapped

    standard: list[FunctionSchema] = []

    for spec in _TOOL_REGISTRY.values():
        if spec.async_tool:
            handler = spec.handler
        else:
            handler = _wrap_sync(spec.handler)
        llm.register_function(
            spec.name, handler, cancel_on_interruption=not spec.async_tool
        )
        standard.append(_schema_for(spec))

    # delegate_to — dynamic schema built per-session from the delegate
    # registry, so it stays out of the @tool registry.
    if delegates and delegates.names():
        llm.register_function(
            "delegate_to",
            _wrap_sync(_delegate_to_handler(
                delegates,
                delivery=delivery,
                push_notification_url=push_notification_url,
                push_notification_token=push_notification_token,
            )),
            cancel_on_interruption=True,
        )
        standard.append(_delegate_to_schema(delegates))

        # delegate_async — fire-and-forget variant (orbis-1s0). Only
        # registered when a DeliveryController exists to surface the result
        # later; without one there'd be no way to speak the answer.
        # async tool → cancel_on_interruption=False so barge-in doesn't kill
        # the in-flight background task; on_finish still fires via _wrap_sync.
        if delivery is not None:
            llm.register_function(
                "delegate_async",
                _wrap_sync(_delegate_async_handler(
                    delegates,
                    delivery=delivery,
                    push_notification_url=push_notification_url,
                    push_notification_token=push_notification_token,
                )),
                cancel_on_interruption=False,
            )
            standard.append(_delegate_async_schema(delegates))

    return ToolsSchema(standard_tools=standard)
