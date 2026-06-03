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
  - ``schedule_reminder`` / ``schedule_recurring_reminder`` /
    ``list_reminders`` / ``cancel_reminder`` — set, review, and cancel
    time-based spoken reminders (one-time and recurring).

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

from .delegates import (
    Delegate,
    DelegateError,
    DelegateRegistry,
    dispatch as delegate_dispatch,
)
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
_HANDWIRED_ASYNC_NAMES = frozenset({"delegate_async", "orchestrate"})

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


# Hand-wired tools whose real latency the registry can't see. delegate_to is
# a synchronous network round-trip to an external agent — it can take tens of
# seconds (Ava ~60s observed) — so it's SLOW: that's what arms the progress-
# narration loop ("still working on that…") so a slow delegate doesn't leave
# the user in dead air after the opening ack.
_HANDWIRED_SLOW_NAMES = frozenset({"delegate_to"})


def latency_for(tool_name: str) -> Latency:
    """Expected latency for a tool — reads the registry. ``delegate_to`` is
    hand-wired and SLOW (external round-trip). Other unknown tools default
    to MEDIUM."""
    spec = _TOOL_REGISTRY.get(tool_name)
    if spec:
        return spec.latency
    if tool_name in _HANDWIRED_SLOW_NAMES:
        return Latency.SLOW
    return Latency.MEDIUM


def capabilities_block(tools_schema) -> str:
    """Generate the 'what you can do' system-prompt section directly from
    the tools actually registered for this session — so it never drifts
    from the code the way a hand-maintained list would.

    A small/fast model will happily *say* it'll do something (set a
    reminder, hand off a task) without invoking the tool, so the result
    never happens. This block names every available action and tells the
    model to call the tool rather than narrate it. The full per-tool schema
    still rides in the request; this is the reinforcement that makes a weak
    model reach for it."""
    tools = getattr(tools_schema, "standard_tools", None) or []
    lines: list[str] = []
    for t in tools:
        name = getattr(t, "name", None)
        if not name:
            continue
        desc = (getattr(t, "description", "") or "").strip()
        # First sentence / first line as the concise trigger hint.
        first = re.split(r"(?<=[.!?])\s|\n", desc, maxsplit=1)[0].strip().rstrip(".")
        if len(first) > 130:
            first = first[:127].rstrip() + "…"
        lines.append(f"- `{name}` — {first}." if first else f"- `{name}`")
    if not lines:
        return ""
    listing = "\n".join(lines)
    return (
        "## WHAT YOU CAN DO — call the tool, don't just say it\n\n"
        "You have tools that take real action. When the user asks for "
        "something one of these does, actually CALL the tool. Promising to do "
        "it WITHOUT calling the tool means it never happens — a reminder you "
        "said you'd set but didn't, a task you said you'd hand off but didn't. "
        "Match the request to the right tool:\n\n"
        f"{listing}"
    )


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


def _humanize_minutes(m: float) -> str:
    if m < 1.5:
        return "in a minute"
    if m < 60:
        return f"in {round(m)} minutes"
    if m < 90:
        return "in about an hour"
    if m < 24 * 60:
        return f"in about {round(m / 60)} hours"
    return f"in about {round(m / (24 * 60))} day(s)"


def _humanize_every(m: float) -> str:
    if m < 1.5:
        return "every minute"
    if m < 60:
        return f"every {round(m)} minutes"
    if m < 90:
        return "every hour"
    if m < 24 * 60:
        return f"every {round(m / 60)} hours"
    return f"every {round(m / (24 * 60))} day(s)"


def _store_reminder(text: str, in_minutes: float, repeat_secs: int | None) -> bool:
    """Persist a reminder. Returns True on success. Shared by the one-time
    and recurring tools so they can't drift apart."""
    from datetime import datetime, timedelta, timezone
    fire_at = (datetime.now(timezone.utc) + timedelta(minutes=in_minutes)).isoformat()
    try:
        from app import get_memory  # type: ignore  # lazy: avoid import cycle
        get_memory().reminders.add(text=text, fire_at=fire_at, repeat_secs=repeat_secs)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[reminder] store failed: {exc}")
        return False
    logger.info(f"[reminder] +{in_minutes}m repeat={repeat_secs}s: {text[:48]!r}")
    return True


@tool(
    "schedule_reminder",
    (
        "Schedule a ONE-TIME spoken reminder — fires once, then it's done. "
        "Use for 'remind me in 10 minutes to take the cookies out', 'in an "
        "hour tell me to call mom', 'remind me at 3 to leave'. Work out "
        "`in_minutes` (minutes from now: 10, 60, 1440 = a day) and the `text` "
        "phrased the way you'd say it back. If — and only if — the user wants "
        "it to REPEAT ('every hour', 'each morning'), use "
        "schedule_recurring_reminder instead, not this one."
    ),
    parameters={
        "in_minutes": {
            "type": "number",
            "description": "Minutes from now until it fires (10, 60, 1440 = a day).",
        },
        "text": {
            "type": "string",
            "description": "What to remind the user, phrased as you'd speak it.",
        },
    },
    required=["in_minutes", "text"],
    latency=Latency.FAST,
)
async def schedule_reminder_handler(params: FunctionCallParams) -> None:
    text = (params.arguments.get("text") or "").strip()
    try:
        in_minutes = float(params.arguments.get("in_minutes"))
    except (TypeError, ValueError):
        in_minutes = 0.0
    if not text or in_minutes <= 0:
        await params.result_callback("I need both a time and what to remind you about.")
        return
    if not _store_reminder(text, in_minutes, None):
        await params.result_callback("I couldn't set that reminder, sorry.")
        return
    await params.result_callback(f"Got it — I'll remind you {_humanize_minutes(in_minutes)}.")


@tool(
    "schedule_recurring_reminder",
    (
        "Schedule a RECURRING spoken reminder that repeats on an interval. "
        "Use ONLY when the user explicitly wants it to repeat ('every hour "
        "remind me to drink water', 'every 30 minutes tell me to look away'). "
        "For a single 'remind me in/at X' use schedule_reminder instead. Pass "
        "`every_minutes` (the interval: 60 = hourly) and `text`. By default "
        "the first one fires after one interval; pass `first_in_minutes` to "
        "start sooner (e.g. 0 to begin right away)."
    ),
    parameters={
        "every_minutes": {
            "type": "number",
            "description": "Repeat interval in minutes (60 = hourly, 30 = half-hourly).",
        },
        "text": {
            "type": "string",
            "description": "What to remind the user, phrased as you'd speak it.",
        },
        "first_in_minutes": {
            "type": "number",
            "description": "Optional. Minutes until the FIRST fire; defaults to every_minutes.",
        },
    },
    required=["every_minutes", "text"],
    latency=Latency.FAST,
)
async def schedule_recurring_reminder_handler(params: FunctionCallParams) -> None:
    text = (params.arguments.get("text") or "").strip()
    try:
        every_minutes = float(params.arguments.get("every_minutes"))
    except (TypeError, ValueError):
        every_minutes = 0.0
    first_raw = params.arguments.get("first_in_minutes")
    try:
        first_in = float(first_raw) if first_raw not in (None, "") else every_minutes
    except (TypeError, ValueError):
        first_in = every_minutes
    if not text or every_minutes <= 0:
        await params.result_callback("I need an interval and what to remind you about.")
        return
    if first_in <= 0:
        first_in = 0.01  # store needs a positive first fire; ~immediate
    if not _store_reminder(text, first_in, int(every_minutes * 60)):
        await params.result_callback("I couldn't set that recurring reminder, sorry.")
        return
    await params.result_callback(
        f"Got it — {_humanize_every(every_minutes)}, starting "
        f"{_humanize_minutes(first_in)}."
    )


def _humanize_reminder(r: dict) -> str:
    """One spoken-friendly line describing a pending reminder."""
    from datetime import datetime, timezone
    text = (r.get("text") or "").rstrip(".")
    repeat = r.get("repeat_secs")
    try:
        fa = datetime.fromisoformat(r["fire_at"])
        mins = (fa - datetime.now(timezone.utc)).total_seconds() / 60
        when = _humanize_minutes(mins) if mins > 0 else "any moment now"
    except Exception:
        when = "soon"
    if repeat:
        return f"{text} — {_humanize_every(repeat / 60)} (next {when})"
    return f"{text} — {when}"


@tool(
    "list_reminders",
    (
        "List the reminders the user currently has scheduled (one-time and "
        "recurring). Use when they ask what reminders or timers are set, or "
        "before cancelling one so you know what's there."
    ),
    parameters={},
    required=[],
    latency=Latency.FAST,
)
async def list_reminders_handler(params: FunctionCallParams) -> None:
    try:
        from app import get_memory  # lazy: avoid import cycle
        pend = get_memory().reminders.pending()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[reminder] list failed: {exc}")
        await params.result_callback("I couldn't check your reminders just now.")
        return
    if not pend:
        await params.result_callback("You don't have any reminders set right now.")
        return
    lines = "; ".join(_humanize_reminder(r) for r in pend)
    n = len(pend)
    await params.result_callback(
        f"You have {n} reminder{'s' if n != 1 else ''}: {lines}."
    )


@tool(
    "cancel_reminder",
    (
        "Cancel a scheduled reminder. Pass `which`: a few words from the "
        "reminder to match (e.g. 'water' cancels 'drink water'), or 'all' to "
        "clear every reminder. Cancels recurring reminders too so they stop "
        "repeating. Call list_reminders first if you're unsure what's set."
    ),
    parameters={
        "which": {
            "type": "string",
            "description": "Words to match the reminder text, or 'all'.",
        },
    },
    required=["which"],
    latency=Latency.FAST,
)
async def cancel_reminder_handler(params: FunctionCallParams) -> None:
    which = (params.arguments.get("which") or "").strip()
    if not which:
        await params.result_callback("Which reminder should I cancel?")
        return
    try:
        from app import get_memory  # lazy: avoid import cycle
        dal = get_memory().reminders
        if which.lower() in ("all", "everything", "every reminder", "them all"):
            n = dal.cancel_all()
            await params.result_callback(
                f"Cleared all {n} reminder{'s' if n != 1 else ''}."
                if n else "There were no reminders to cancel."
            )
            return
        cancelled = dal.cancel_matching(which)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[reminder] cancel failed: {exc}")
        await params.result_callback("I couldn't cancel that, sorry.")
        return
    if not cancelled:
        await params.result_callback(
            f"I didn't find a reminder matching '{which}'."
        )
    elif len(cancelled) == 1:
        await params.result_callback(
            f"Done — cancelled the reminder to {cancelled[0]['text'].rstrip('.').lower()}."
        )
    else:
        await params.result_callback(
            f"Cancelled {len(cancelled)} reminders matching '{which}'."
        )


# ---------------------------------------------------------------------------
# render_widget — show/hide a subtle on-screen widget by voice. ORBIS stays
# voice-first; the widget is an ambient glance readout, not an app. The frontend
# opens it (and seeds its state) off the SSE 'widget' event.
# ---------------------------------------------------------------------------

# What the model may render. Keep in step with the web/src/widgets registrations;
# the description teaches the model each widget's settable props.
_RENDERABLE_WIDGETS = "weather (props: location — a city name)"
_KNOWN_WIDGETS = frozenset({"weather"})


@tool(
    "render_widget",
    (
        "Show or hide a small ambient on-screen widget for the user to glance at. "
        "Use when they want to SEE something — 'show me the weather', 'pull up the "
        "weather in Tokyo', 'hide the weather'. The widget is a subtle floating "
        "readout, so still speak your normal short answer too. "
        f"Available widgets: {_RENDERABLE_WIDGETS}."
    ),
    parameters={
        "widget": {"type": "string", "description": "Widget id, e.g. 'weather'."},
        "action": {
            "type": "string",
            "enum": ["open", "close"],
            "description": "open (default) to show it, close to hide it.",
        },
        "location": {
            "type": "string",
            "description": "For the weather widget: the city to show (optional).",
        },
    },
    required=["widget"],
    latency=Latency.FAST,
)
async def render_widget_handler(params: FunctionCallParams) -> None:
    from voice.sse_bus import sse_bus

    widget = (params.arguments.get("widget") or "").strip().lower()
    action = (params.arguments.get("action") or "open").strip().lower()
    if widget not in _KNOWN_WIDGETS:
        await params.result_callback(f"I don't have a {widget} widget to show.")
        return
    if action not in ("open", "close"):
        action = "open"
    props: dict[str, str] = {}
    location = (params.arguments.get("location") or "").strip()
    if location:
        props["location"] = location
    await sse_bus.publish("widget", {"action": action, "id": widget, "props": props})
    if action == "close":
        await params.result_callback(f"Hidden the {widget}.")
    elif location:
        await params.result_callback(f"Here's the {widget} for {location}.")
    else:
        await params.result_callback(f"Here's the {widget}.")


# render_widget is part of the experimental on-screen "surface" (command bar,
# widgets, ambient mini-orb), gated behind ORBIS_SURFACE while it bakes. When
# off, drop it from the registry so it isn't offered to the LLM — the frontend
# widget dock is gated too, so a rendered widget wouldn't show anyway.
if os.environ.get("ORBIS_SURFACE") not in ("1", "true", "on"):
    _TOOL_REGISTRY.pop("render_widget", None)


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


def _spawn_delegate_delivery(
    delegate: Delegate,
    query: str,
    *,
    delivery: DeliveryController,
    target: str,
    push_notification_url: str | None = None,
    push_notification_token: str | None = None,
) -> None:
    """Dispatch a delegate in the BACKGROUND and deliver its result through the
    DeliveryController when it lands (TIME_SENSITIVE → spoken at the next natural
    pause). Shared by delegate_async and by delegate_to's A2A path.

    A2A delegates are async tasks — a buffer-then-answer gateway (Ava routes
    through the fleet) can take seconds. Holding the voice turn on one made ORBIS
    go silent mid-conversation and let context summarization collide with the
    in-flight tool call. Backgrounding it is the A2A-native fix: the turn ends
    immediately, the answer arrives as a fresh turn."""
    # Read at call time so env overrides (and tests) take effect per dispatch.
    bg_timeout = float(os.environ.get("DELEGATE_ASYNC_TIMEOUT", "300"))
    # Proactive follow-up (orbis-29q): nudge once if a hand-off outlives this so
    # a slow delegate doesn't feel forgotten. Cancelled the moment it lands.
    nudge_secs = float(os.environ.get("DELEGATE_NUDGE_SECS", "90"))

    async def _run_and_deliver() -> None:
        done = asyncio.Event()

        async def _nudge_if_slow() -> None:
            if nudge_secs <= 0:
                return
            try:
                await asyncio.sleep(nudge_secs)
                if not done.is_set():
                    await delivery.deliver(
                        f"still waiting to hear back from {target} on that",
                        priority=Priority.TIME_SENSITIVE, source=target,
                        kind="delegate",
                        cooldown_key=f"delegate-nudge:{target}:{query[:40]}",
                    )
            except asyncio.CancelledError:
                pass

        async def _progress(_msg: str) -> None:
            # Grounded progress: a real A2A WORKING heartbeat (streaming) means
            # the delegate is genuinely still on it. Speak one short reassurance
            # (once, via cooldown) + update the visual rail — replaces the old
            # blind canned "Ava's checking…" filler with a real-event-driven one.
            try:
                await delivery.note_progress(f"{target} working", source=target)
            except Exception:  # noqa: BLE001
                pass
            try:
                await delivery.deliver(
                    f"{target}'s still working on that",
                    priority=Priority.TIME_SENSITIVE, source=target, kind="delegate",
                    cooldown_key=f"delegate-working:{target}",
                )
            except Exception:  # noqa: BLE001
                pass

        nudge = asyncio.create_task(_nudge_if_slow())
        try:
            result = await delegate_dispatch(
                delegate, query,
                timeout=bg_timeout,
                progress_callback=_progress,
                push_notification_url=push_notification_url,
                push_notification_token=push_notification_token,
            )
            await delivery.deliver(
                _strip_markdown_for_speech(result),
                priority=Priority.TIME_SENSITIVE, source=target, kind="delegate",
            )
        except DelegateError as e:
            logger.warning(f"[delegate.bg] {target} failed: {e}")
            await delivery.deliver(
                f"that thing I handed off to {target} didn't go through — {e}",
                priority=Priority.TIME_SENSITIVE, source=target,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[delegate.bg] {target} errored: {e}")
            await delivery.deliver(
                f"that thing I handed off to {target} errored out.",
                priority=Priority.TIME_SENSITIVE, source=target,
            )
        finally:
            done.set()
            nudge.cancel()

    task = asyncio.create_task(_run_and_deliver())
    _BG_DELEGATE_TASKS.add(task)
    task.add_done_callback(_BG_DELEGATE_TASKS.discard)


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

        # A2A delegates are async tasks (Ava buffers-then-answers through the
        # fleet). NEVER hold the voice turn on one: dispatch in the background
        # and deliver the answer via the DeliveryController when it lands; end
        # the turn now with a short ack. Holding the turn here is what made
        # ORBIS go silent mid-conversation and let context summarization collide
        # with the in-flight tool call.
        if delegate.type == "a2a" and delivery is not None:
            _spawn_delegate_delivery(
                delegate, query, delivery=delivery, target=target,
                push_notification_url=push_notification_url,
                push_notification_token=push_notification_token,
            )
            await params.result_callback(
                f"On it — asking {target} now; I'll tell you what they say."
            )
            return

        # Synchronous relay for fast local delegates (ACP / OpenAI), or an A2A
        # delegate with no delivery channel to surface an async result.
        # Intermediate status streams to the VISUAL rail (StatusPill via
        # delegation-progress SSE) — NOT spoken.
        progress_cb = None
        if delivery is not None and delegate.type == "a2a":
            async def _progress(msg: str) -> None:
                await delivery.note_progress(msg, source=target)
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
    natural pause) when the delegate finishes. Shares the background-deliver
    substrate with delegate_to's A2A path (`_spawn_delegate_delivery`)."""

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
        _spawn_delegate_delivery(
            delegate, query, delivery=delivery, target=target,
            push_notification_url=push_notification_url,
            push_notification_token=push_notification_token,
        )

        # Immediate ack — the LLM speaks this and the turn ends; the answer
        # arrives later via the DeliveryController.
        await params.result_callback(
            f"Sent that off to {target}. I'll let you know as soon as they're back."
        )

    return _handler


# ---------------------------------------------------------------------------
# orchestrate — multi-step delegation loop (D1). Ack now, drive a bounded
# loop chaining several delegate hand-offs toward a goal in the background,
# deliver the synthesized result. The loop itself lives in agent/orchestrate.py
# and is injected as `runner` so this module needn't know about the LLM client.
# ---------------------------------------------------------------------------

# runner(goal: str, *, progress, ask_user) -> Awaitable[str]
OrchestrateRunner = Any

# How long a HITL ask_user pause waits for the user's spoken answer before the
# run gives up (so a walked-away user can't wedge a background run forever).
_ASK_USER_TIMEOUT_S = float(os.environ.get("ORCHESTRATE_ASK_TIMEOUT", "300"))


def _orchestrate_schema(registry: DelegateRegistry) -> FunctionSchema:
    items = registry.all()
    target_lines = "\n".join(f"  - {d.name}: {d.description}" for d in items)
    return FunctionSchema(
        name="orchestrate",
        description=(
            "Drive a MULTI-STEP goal that needs several coordinated hand-offs "
            "to your agents — research that builds on itself, 'find X then dig "
            "into the result', or comparing answers from more than one agent. "
            "Runs in the BACKGROUND: acknowledge you're on it and will report "
            "back; do NOT wait or invent the result. ORBIS chains the steps and "
            "speaks the synthesized answer when done.\n\n"
            "Choose the right tool: `delegate_to` for a SINGLE quick question, "
            "`delegate_async` for ONE longer hand-off, `orchestrate` only when "
            "the goal genuinely needs MULTIPLE steps.\n\n"
            f"Available agents:\n{target_lines}\n\n"
            "Pass `goal` — the overall objective in plain language."
        ),
        properties={
            "goal": {
                "type": "string",
                "description": "The multi-step objective, in plain language",
            },
        },
        required=["goal"],
    )


def _orchestrate_handler(
    registry: DelegateRegistry,
    *,
    delivery: DeliveryController,
    runner: OrchestrateRunner,
):
    """Async tool: ack now, run the bounded orchestration loop in the
    background, deliver the synthesized result via the DeliveryController."""

    async def _handler(params: FunctionCallParams) -> None:
        goal = (params.arguments.get("goal") or "").strip()
        if not goal:
            await params.result_callback("I need a goal to work toward.")
            return

        logger.info(f"[orchestrate] goal={goal!r} (background)")

        async def _progress(text: str) -> None:
            # One short "still working" style step note. The DeliveryController
            # gates timing; the cooldown_key collapses identical step notes.
            await delivery.deliver(
                text,
                priority=Priority.TIME_SENSITIVE,
                source="orchestrator",
                kind="orchestrate",
                cooldown_key=f"orchestrate-step:{text[:40]}",
            )

        async def _ask_user(question: str) -> str:
            # HITL: speak the question, park a pending-ask on the live session,
            # and wait for the voice AskGate to resolve it with the user's next
            # transcript. Times out so a walked-away user can't wedge the run.
            from agent.user_state import (
                PendingAsk,
                set_pending_ask_on_active,
                take_pending_ask,
            )

            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            if not set_pending_ask_on_active(PendingAsk(question=question, future=fut)):
                return "(no live session to ask the user — proceed from the goal)"
            await delivery.deliver(
                question,
                priority=Priority.TIME_SENSITIVE,
                source="orchestrator",
                kind="orchestrate-ask",
            )
            try:
                return await asyncio.wait_for(fut, timeout=_ASK_USER_TIMEOUT_S)
            finally:
                take_pending_ask()  # clear if it timed out / wasn't answered

        async def _run_and_deliver() -> None:
            try:
                result = await runner(goal, progress=_progress, ask_user=_ask_user)
                await delivery.deliver(
                    _strip_markdown_for_speech(result),
                    priority=Priority.TIME_SENSITIVE,
                    source="orchestrator",
                    kind="orchestrate",
                )
            except Exception as e:
                logger.exception(f"[orchestrate] goal failed: {e}")
                await delivery.deliver(
                    "that multi-step thing I was working on ran into trouble.",
                    priority=Priority.TIME_SENSITIVE,
                    kind="orchestrate",
                )

        task = asyncio.create_task(_run_and_deliver())
        _BG_DELEGATE_TASKS.add(task)
        task.add_done_callback(_BG_DELEGATE_TASKS.discard)

        await params.result_callback(
            "On it — I'll work through that and let you know what I find."
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
    orchestrate_runner: OrchestrateRunner | None = None,
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

            # orchestrate — multi-step delegation loop (D1). Needs both a
            # DeliveryController (to speak the synthesis) and the injected
            # runner (the loop + LLM client, built in app.py).
            if orchestrate_runner is not None:
                llm.register_function(
                    "orchestrate",
                    _wrap_sync(_orchestrate_handler(
                        delegates,
                        delivery=delivery,
                        runner=orchestrate_runner,
                    )),
                    cancel_on_interruption=False,
                )
                standard.append(_orchestrate_schema(delegates))

    return ToolsSchema(standard_tools=standard)
