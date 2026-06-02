"""D1 orchestration loop — bounded, router-first, multi-step delegation.

`orchestrate(goal)` is the agency centrepiece (docs/internal/duplex-orchestration-direction.md).
It runs a bounded ReAct loop that chains several delegate hand-offs toward a
goal and synthesises one answer. The **heavy reasoning stays in the delegates**
(DECISIONS.md:59) — this loop only decides *which* agent to ask *what* next, and
weaves the results together. ORBIS orchestrates; it does not reason in-process.

Key property vs the one-shot `delegate_to`: each delegate gets a **sticky
``contextId`` for the whole run** (one `A2AClient` per delegate, constructed
here), so a follow-up step to the same agent lands in the same A2A conversation
and the agent remembers what it already told us. A fresh run gets fresh clients,
so goals never cross-contaminate.

Localhost reality: every delegate step is bounded by ``asyncio.wait_for`` and we
hold the stream / let `A2AClient` poll — the loop never depends on a push
callback to make progress (cloud delegates can't reach 127.0.0.1; real push is
deferred to tailnet-live).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable

from a2a_outbound import A2AClient, A2ADispatchError
from agent.delegates import DelegateRegistry
from agent.tools import build_text_tool_schemas, run_text_tool

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], Awaitable[None]]

# Bounded so a runaway plan can't loop forever; higher than the A2A inbound
# ReAct cap (3) since a multi-step goal legitimately needs several hand-offs.
_MAX_ITER = int(os.environ.get("ORCHESTRATE_MAX_ITER", "6"))
# Per-step wall-clock bound for one delegate hand-off.
_STEP_TIMEOUT = float(os.environ.get("ORCHESTRATE_STEP_TIMEOUT", "120"))

_SYSTEM = """\
You are ORBIS, orchestrating a multi-step goal by delegating to the user's
agents. You do NOT do the heavy work yourself — you decide which agent to ask,
with what, and in what order, then synthesise their answers.

THE GOAL:
{goal}

YOUR AGENTS:
{agents}

HOW TO WORK:
- Use the `delegate_to` tool to hand a concrete sub-task to one agent. Break the
  goal into steps; each step's result informs the next (you may ask the same
  agent again — it remembers this conversation).
- Keep going until the goal is met, then STOP calling tools and write the final
  answer: a single, spoken-aloud synthesis (2-4 sentences, plain text, no
  markdown). Lead with what the user actually wanted to know.
- If an agent asks for clarification you can answer from the goal, answer it.
  If you truly can't proceed, summarise what you got and what's blocking.
- Don't pad. If one hand-off already answers the goal, synthesise and stop.
"""


async def run_orchestration(
    goal: str,
    *,
    delegates: DelegateRegistry,
    client,
    model: str,
    extra_body: dict | None = None,
    max_tokens: int = 512,
    temperature: float = 0.4,
    progress: ProgressFn | None = None,
    max_iter: int = _MAX_ITER,
) -> str:
    """Drive the bounded orchestration loop and return the synthesised answer.

    ``client`` is an AsyncOpenAI-compatible client (the session's text LLM).
    ``progress`` (optional) is narrated to the user between steps.
    """
    # One sticky-context client per delegate, for THIS run only.
    run_clients: dict[str, A2AClient] = {}

    def _client_for(name: str) -> A2AClient | None:
        if name in run_clients:
            return run_clients[name]
        d = delegates.get(name)
        if d is None or d.type != "a2a":
            return None
        c = A2AClient(
            d.url,
            headers=d.auth_headers(),
            card_origin=d.origin(),
            name=d.name,
        )
        run_clients[name] = c
        return c

    agent_lines = "\n".join(f"  - {d.name}: {d.description}" for d in delegates.all())
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM.format(goal=goal, agents=agent_lines)},
        {"role": "user", "content": goal},
    ]
    tools_openai = build_text_tool_schemas(delegates)

    final = ""
    for step in range(max(1, max_iter)):
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "tools": tools_openai,
            "tool_choice": "auto",
        }
        if extra_body is not None:
            kwargs["extra_body"] = extra_body
        r = await client.chat.completions.create(**kwargs)
        msg = r.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            final = (msg.content or "").strip()
            break

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            name = tc.function.name
            out = await _run_step(
                name, args, delegates=delegates, client_for=_client_for,
                progress=progress, step=step,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": out,
            })
    else:
        logger.warning(
            f"[orchestrate] hit step limit ({max_iter}) without a final answer"
        )
        final = final or (
            "I worked through several steps but didn't reach a clean answer "
            "in the steps I allow myself — want me to keep going?"
        )

    return final or "I wasn't able to get a useful result on that, sorry."


async def _run_step(
    name: str,
    args: dict,
    *,
    delegates: DelegateRegistry,
    client_for: Callable[[str], A2AClient | None],
    progress: ProgressFn | None,
    step: int,
) -> str:
    """Execute one tool call inside the loop. ``delegate_to`` is routed through
    the per-run sticky client (multi-turn continuity); every other tool falls
    back to the shared text-mode runner."""
    if name == "delegate_to":
        target = (args.get("target") or "").strip()
        query = (args.get("query") or "").strip()
        client = client_for(target)
        if client is None:
            return f"(no such agent: {target!r})"
        if not query:
            return f"(empty query for {target})"
        logger.info(f"[orchestrate] step={step} delegate_to {target} q={query!r}")
        if progress is not None:
            await _safe_progress(progress, f"checking with {target}")
        try:
            res = await asyncio.wait_for(client.send(query), timeout=_STEP_TIMEOUT)
        except (A2ADispatchError, asyncio.TimeoutError) as e:
            logger.warning(f"[orchestrate] {target} step failed: {e}")
            return f"({target} couldn't answer that step: {e})"
        if res.input_required:
            # Surface the question back into the loop so the orchestrating model
            # can answer it (the sticky context keeps it in the same A2A
            # conversation). Full same-taskId continuation is a follow-up.
            return f"[{target} needs more to proceed] {res.text}"
        return res.text or f"({target} returned nothing)"

    # Read-only / local tools (calculator, datetime, web_search, …).
    return await run_text_tool(name, args, delegates=delegates)


async def _safe_progress(progress: ProgressFn, text: str) -> None:
    try:
        await progress(text)
    except Exception as e:  # noqa: BLE001 — progress is best-effort narration
        logger.debug(f"[orchestrate] progress narration failed: {e}")
