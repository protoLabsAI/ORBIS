"""Tool registry for the voice agent.

New tools register themselves via the ``@tool()`` decorator — no edits
to ``register_tools`` or hardcoded latency dicts required. The registry
is the single source of truth for:

  - name / description / JSON-schema parameters
  - latency tier (drives progress-narration cadence in DeliveryController)
  - sync vs async (``async_tool=True`` → ``cancel_on_interruption=False``
    and the LLM context gets a deferred result injection on completion)

Example:

    @tool(
        "calculator",
        "Evaluate an arithmetic expression",
        parameters={"expression": {"type": "string", "description": "..."}},
        required=["expression"],
        latency=Latency.FAST,
    )
    async def calculator_handler(params): ...

Tools that need runtime context (``slow_research`` closes over the
DeliveryController; ``delegate_to`` closes over the DelegateRegistry +
push-notification config) are still hand-wired in ``register_tools``.
That keeps the decorator simple for 95% of tools while leaving an
escape hatch for the context-heavy 5%.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import operator
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams, LLMService

from .delegates import DelegateError, DelegateRegistry, dispatch as delegate_dispatch
from .delivery import DeliveryController
from .filler import Latency

logger = logging.getLogger(__name__)

# Tunables — let us stress-test filler + progress + delivery independently.
SLOW_RESEARCH_SECS = float(os.environ.get("SLOW_RESEARCH_SECS", "20"))
DEFAULT_TZ = os.environ.get("TZ", "America/New_York")


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
    so it also falls back to MEDIUM, which matches the historical value."""
    spec = _TOOL_REGISTRY.get(tool_name)
    return spec.latency if spec else Latency.MEDIUM


# Derived from the registry. Lets app.py keep a `name in ASYNC_TOOL_NAMES`
# style check without maintaining a parallel frozenset.
class _AsyncToolNames:
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
# Built-in tools — decorated in-place so they self-register.
# ---------------------------------------------------------------------------

_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        op = _ALLOWED_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_safe_eval(node.operand)
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")


@tool(
    "calculator",
    (
        "Evaluate a basic arithmetic expression. Use ONLY for "
        "calculations the user has explicitly asked you to do."
    ),
    parameters={
        "expression": {
            "type": "string",
            "description": "Arithmetic expression, e.g. '15 * 1.2 + 3'",
        },
    },
    required=["expression"],
    latency=Latency.FAST,
)
async def calculator_handler(params: FunctionCallParams) -> None:
    expr = params.arguments.get("expression", "")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
        val = _safe_eval(tree.body)
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        result = f"{expr} equals {val}."
    except Exception as e:
        result = f"I couldn't calculate that: {e}."
    await params.result_callback(result)


@tool(
    "get_datetime",
    "Return the current date and time.",
    latency=Latency.FAST,
)
async def datetime_handler(params: FunctionCallParams) -> None:
    try:
        tz = ZoneInfo(DEFAULT_TZ)
    except (ZoneInfoNotFoundError, Exception):
        tz = ZoneInfo("UTC")
    result = datetime.now(tz=tz).strftime(
        "It's %A, %B %d, %Y at %I:%M %p %Z."
    )
    await params.result_callback(result)


@tool(
    "web_search",
    (
        "Search the web for current information. Use when the user asks "
        "about news, recent events, or facts you're not confident about. "
        "Returns short snippets from the top results."
    ),
    parameters={
        "query": {"type": "string", "description": "Search query"},
    },
    required=["query"],
    latency=Latency.MEDIUM,
)
async def web_search_handler(params: FunctionCallParams) -> None:
    query = params.arguments.get("query", "").strip()
    if not query:
        await params.result_callback("No search query provided.")
        return

    def _search() -> str:
        from ddgs import DDGS
        with DDGS() as d:
            results = list(d.text(query, max_results=5))
        if not results:
            return "No results found."
        lines = []
        for r in results:
            title = r.get("title") or ""
            body = r.get("body") or ""
            lines.append(f"{title}: {body}")
        return " ".join(lines)

    try:
        text = await asyncio.to_thread(_search)
    except Exception as e:
        logger.warning(f"[web_search] failed: {e}")
        text = "The search failed — I couldn't reach DuckDuckGo."
    await params.result_callback(text[:2000])  # keep context manageable


# ---------------------------------------------------------------------------
# Async tool — in the registry so latency_for() + ASYNC_TOOL_NAMES pick it
# up correctly. The decorated handler is a placeholder; register_tools
# swaps in the real one that closes over the DeliveryController.
# ---------------------------------------------------------------------------

@tool(
    "slow_research",
    (
        "Kick off a long-running investigation (30s+). Use when the user "
        "doesn't need an immediate answer — they can keep chatting while "
        "the agent will speak the result when it's ready."
    ),
    parameters={"query": {"type": "string", "description": "The question to investigate"}},
    required=["query"],
    latency=Latency.SLOW,
    async_tool=True,
)
async def _slow_research_placeholder(params: FunctionCallParams) -> None:
    # Never actually invoked — register_tools substitutes the real handler
    # built from the session's DeliveryController.
    raise RuntimeError("slow_research placeholder called without substitution")


# ---------------------------------------------------------------------------
# delegate_to — hand-wired because its schema is dynamic per-session
# (derived from the live, per-skill-filtered DelegateRegistry).
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
            "Hand off a question to a specialized backend — another agent "
            "in the fleet, or a heavier reasoning model. Use when the "
            "user's question genuinely requires depth, current info, or "
            "another specialist's expertise.\n\n"
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


def _slow_research_handler(_controller: DeliveryController):
    """Async tool — the LLM acknowledges via its inline preamble (M10
    TOOL USE prompt block). DO NOT call result_callback in the foreground;
    pipecat would treat that as the finished result and the LLM would
    fabricate follow-ups about the topic."""
    async def _handler(params: FunctionCallParams) -> None:
        query = params.arguments.get("query", "")
        logger.info(f"[slow_research] starting: {query!r} (sleep {SLOW_RESEARCH_SECS}s)")

        async def _background() -> None:
            await asyncio.sleep(SLOW_RESEARCH_SECS)
            result = (
                f"Investigation into '{query}' complete. "
                "This is a synthetic placeholder — real long-form research "
                "lands when this tool gets a real backend."
            )
            try:
                await params.result_callback(result)
                logger.info(f"[slow_research] result_callback fired ({len(result)} chars)")
            except Exception as e:
                logger.exception(f"[slow_research] result_callback failed: {e}")

        asyncio.create_task(_background())

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
    result_callback. Used by the inbound A2A ReAct loop (F6) so external
    agents can drive the same tool registry the voice path uses.

    Sync tools that are in the ``@tool``-decorated registry resolve
    automatically. ``delegate_to`` is hand-handled. Async tools
    (slow_research) are NOT exposed here — they require a live
    DeliveryController + voice session to narrate back on completion.
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
    minus slow_research (async — see run_text_tool)."""
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
# the context-heavy ones.
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

    Decorated tools (``@tool``) are registered automatically. ``delegate_to``
    and ``slow_research`` are hand-wired because they close over a runtime
    controller / registry.
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

    # Registry-driven tools. slow_research needs runtime delivery context;
    # skip it when no delivery controller is attached, otherwise substitute
    # the real handler for its placeholder.
    for spec in _TOOL_REGISTRY.values():
        if spec.name == "slow_research":
            if delivery is None:
                continue
            handler = _slow_research_handler(delivery)
        elif spec.async_tool:
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
