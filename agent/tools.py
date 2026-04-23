"""Tool registry for the ORBIS voice agent.

ORBIS's tool surface is deliberately narrow:

  - ``delegate_to(target, query)`` — hand off to a user-configured agent
    (A2A or OpenAI-compat). The user's configured agents are where heavy
    reasoning lives; ORBIS is the voice frontend for them.
  - ``set_variant`` / ``apply_palette`` / ``adjust_param`` /
    ``save_preset`` / ``recall_preset`` — orb self-modification. Lets the
    agent change its own appearance in response to user requests
    ("be warmer", "try a darker palette"). The tool handler returns a
    short spoken confirmation; the client-side orb plugin listens for
    the function-call RTVI event and applies the visual change.

Nothing else ships: no calculator, no search, no datetime, no
fetch_url — those all become user-configured delegates if actually
needed. Tools that would create support burden without being the
product's differentiator are excluded by design.

Registration: tools self-register via ``@tool(...)`` at import time.
``delegate_to`` is hand-wired because its JSON schema enumerates the
live delegate registry and therefore changes per-session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams, LLMService

from .delegates import DelegateError, DelegateRegistry, dispatch as delegate_dispatch
from .delivery import DeliveryController
from .filler import Latency

logger = logging.getLogger(__name__)


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
        spec = _TOOL_REGISTRY.get(name)
        return bool(spec and spec.async_tool)

    def __iter__(self):
        return iter(
            name for name, spec in _TOOL_REGISTRY.items() if spec.async_tool
        )


ASYNC_TOOL_NAMES = _AsyncToolNames()


def _schema_for(spec: ToolSpec) -> FunctionSchema:
    return FunctionSchema(
        name=spec.name,
        description=spec.description,
        properties=spec.parameters,
        required=spec.required,
    )


# ---------------------------------------------------------------------------
# Orb self-modification tools.
#
# These tools don't touch server-side orb state directly. They return a
# short spoken confirmation (so the LLM can say it aloud), and the
# client-side orb plugin listens for the matching RTVI function-call
# event over the WebRTC data channel and applies the visual change.
#
# Entitlement gating (free vs paid tier) for non-starter variants /
# palettes is handled at the route / client layer, not here — tool
# handlers just describe the intent.
# ---------------------------------------------------------------------------


@tool(
    "set_variant",
    (
        "Switch the orb to a different base visual variant. Use when the "
        "user asks for a fundamentally different look (e.g. 'try a nebula' "
        "or 'show me the crystal'). Available variants depend on the user's "
        "entitlement; pick from the orb's registered set."
    ),
    parameters={
        "name": {
            "type": "string",
            "description": "Variant name (e.g. 'fractal', 'nebula', 'crystal', 'particles')",
        },
    },
    required=["name"],
    latency=Latency.FAST,
)
async def set_variant_handler(params: FunctionCallParams) -> None:
    name = (params.arguments.get("name") or "").strip().lower()
    if not name:
        await params.result_callback("I need a variant name to try.")
        return
    logger.info(f"[orb] set_variant → {name!r}")
    await params.result_callback(f"Trying the {name} variant.")


@tool(
    "apply_palette",
    (
        "Apply a named color palette to the orb's current variant. Use when "
        "the user wants a mood shift without changing the base form ('warmer', "
        "'darker', 'something moody'). Unknown palette names fall through "
        "gracefully on the client."
    ),
    parameters={
        "name": {
            "type": "string",
            "description": "Palette name (e.g. 'Aurora', 'Ember', 'Noir', 'Mono')",
        },
    },
    required=["name"],
    latency=Latency.FAST,
)
async def apply_palette_handler(params: FunctionCallParams) -> None:
    name = (params.arguments.get("name") or "").strip()
    if not name:
        await params.result_callback("I need a palette name.")
        return
    logger.info(f"[orb] apply_palette → {name!r}")
    await params.result_callback(f"Switching to the {name} palette.")


@tool(
    "adjust_param",
    (
        "Tweak a single shader parameter on the current orb. Use for "
        "incremental adjustments the user suggests ('a bit more turbulent', "
        "'slower pulse'). Values outside the parameter's valid range are "
        "clamped by the client."
    ),
    parameters={
        "key": {
            "type": "string",
            "description": "Parameter name (e.g. 'density', 'speed', 'glowIntensity')",
        },
        "value": {
            "type": "number",
            "description": "New value for the parameter",
        },
    },
    required=["key", "value"],
    latency=Latency.FAST,
)
async def adjust_param_handler(params: FunctionCallParams) -> None:
    key = (params.arguments.get("key") or "").strip()
    value = params.arguments.get("value")
    if not key or value is None:
        await params.result_callback("I need both a parameter name and a value.")
        return
    logger.info(f"[orb] adjust_param → {key}={value!r}")
    await params.result_callback(f"Adjusting {key}.")


@tool(
    "save_preset",
    (
        "Save the orb's current configuration (variant + palette + params) "
        "as a named preset. Use when the user says something like 'save this "
        "as calm mode' or 'remember this look'. Names are case-insensitive."
    ),
    parameters={
        "name": {
            "type": "string",
            "description": "Preset name (e.g. 'calm', 'focused', 'sleepy')",
        },
    },
    required=["name"],
    latency=Latency.FAST,
)
async def save_preset_handler(params: FunctionCallParams) -> None:
    name = (params.arguments.get("name") or "").strip()
    if not name:
        await params.result_callback("I need a name to save under.")
        return
    logger.info(f"[orb] save_preset → {name!r}")
    await params.result_callback(f"Saved this as {name}.")


@tool(
    "recall_preset",
    (
        "Restore a previously saved preset by name. Use when the user asks "
        "to go back to a saved look ('go back to calm mode'). If the preset "
        "doesn't exist the client quietly no-ops and surfaces a notification."
    ),
    parameters={
        "name": {
            "type": "string",
            "description": "Preset name to recall",
        },
    },
    required=["name"],
    latency=Latency.FAST,
)
async def recall_preset_handler(params: FunctionCallParams) -> None:
    name = (params.arguments.get("name") or "").strip()
    if not name:
        await params.result_callback("I need a preset name to recall.")
        return
    logger.info(f"[orb] recall_preset → {name!r}")
    await params.result_callback(f"Going back to {name}.")


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
            await params.result_callback(result)
        except DelegateError as e:
            await params.result_callback(f"Couldn't reach {target}: {e}")
        except Exception as e:
            logger.exception(f"[delegate_to] unexpected error: {e}")
            await params.result_callback(f"Delegation to {target} errored: {e}")

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

    return ToolsSchema(standard_tools=standard)
