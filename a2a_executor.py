"""ORBIS's A2A 1.0 AgentExecutor — drives the inbound ReAct brain via a2a-sdk.

Replaces the hand-rolled ``a2a/server.py`` request handling. ``a2a-sdk`` owns
every piece of protocol mechanics (JSON-RPC dispatch, SSE streaming, the task
lifecycle, push delivery, the task store). This module is the bridge: it adapts
ORBIS's inbound answer loop — exposed as a ``(event_type, payload)`` async
generator (the *stream factory*, built in ``app.py`` from the ReAct loop that
used to be ``text_agent``) — onto the SDK's ``EventQueue`` via ``TaskUpdater``,
and emits the four protoLabs extensions through ``protolabs_a2a``.

Ported from protoAgent#453's ``a2a_executor.py`` (the fleet reference). The
producer-event contract is host-agnostic::

    text            accumulated answer text (streamed)
    tool_start      a tool began      (dict {id,name,input} | str)
    tool_end        a tool finished   (dict {id,name,output} | str)
    delta           a worldstate-delta {domain,path,op,value}
    usage           per-LLM-call token usage {input_tokens,output_tokens,...}
    confidence      self-reported {confidence, explanation?}
    input_required  HITL pause {question}
    done            terminal; payload is the final text
    error           terminal; payload is the error string

On terminal completion the accumulated text + the cost / confidence /
worldstate-delta extension DataParts are published as a single artifact. Tool
events surface as tool-call-v1 DataParts on the working status frames.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus
from google.protobuf import json_format, struct_pb2

import protolabs_a2a as pa

logger = logging.getLogger(__name__)


@dataclass
class TurnOutcome:
    """Everything the host needs at the end of an A2A turn (ADR-0006).

    Passed to the registered terminal hook so the host can surface the answer
    + record per-turn telemetry without the executor depending on either.
    """

    task_id: str
    context_id: str
    state: str  # "completed" | "failed"
    text: str
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_ms: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    models: list[str] = field(default_factory=list)


# A terminal hook the host can register: invoked with a ``TurnOutcome`` when a
# turn reaches a terminal state. No-op when unset.
_ON_TERMINAL: list[Callable[[TurnOutcome], None] | None] = [None]


def set_terminal_hook(hook: Callable[[TurnOutcome], None] | None) -> None:
    """Register (or clear) the terminal hook fired on task completion."""
    _ON_TERMINAL[0] = hook


def _notify_terminal(outcome: TurnOutcome) -> None:
    cb = _ON_TERMINAL[0]
    if cb is None:
        return
    try:
        cb(outcome)
    except Exception:  # noqa: BLE001 — best-effort, never breaks the turn
        logger.exception("[a2a] terminal hook failed for context %s", outcome.context_id)


def _text_part(text: str) -> Part:
    return Part(text=text)


def _data_part_proto(payload: Any, mime_type: str) -> Part:
    """A proto ``Part`` carrying ``payload`` under ``mime_type``.

    ``a2a-sdk`` serializes this to the A2A 1.0 wire shape
    ``{"data": …, "metadata": {"mimeType": …}, "mediaType": "application/json"}``.
    """
    part = Part()
    value = struct_pb2.Value()
    json_format.ParseDict(payload, value.struct_value)
    part.data.CopyFrom(value)
    part.metadata.update({pa.MIME_KEY: mime_type})
    part.media_type = pa.DATA_MEDIA_TYPE
    return part


def _ext_data_part(emit_dict: dict[str, Any]) -> Part:
    """Convert a ``protolabs_a2a.emit_*`` contract dict into a proto ``Part``."""
    mime = emit_dict["metadata"][pa.MIME_KEY]
    payload = emit_dict["content"]["value"]
    return _data_part_proto(payload, mime)


class OrbisAgentExecutor(AgentExecutor):
    """Bridges ORBIS's inbound answer stream onto the A2A event queue.

    One ``execute`` call runs one turn end-to-end (or to a HITL pause).
    ``cancel`` marks the task canceled — the framework cancels the in-flight
    ``execute`` coroutine and the stream unwinds.
    """

    def __init__(
        self,
        stream_fn_factory: Callable[..., AsyncGenerator[tuple[str, Any], None]],
    ) -> None:
        # ``stream_fn_factory(text, context_id, *, resume, caller_trace)`` →
        # async generator of (event_type, payload). Built in app.py from the
        # inbound ReAct loop.
        self._stream_factory = stream_fn_factory

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        resume = bool(context.current_task and _is_input_required(context.current_task))
        if not resume:
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                )
            )
        await updater.start_work()

        text = context.get_user_input()
        caller_trace = _extract_caller_trace(context)

        started = time.monotonic()
        accumulated = ""
        deltas: list[dict] = []
        usage = {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        }
        cost_usd = 0.0
        had_usage = False
        confidence: float | None = None
        confidence_expl: str | None = None
        llm_calls = 0
        tool_calls = 0
        models: list[str] = []

        def _outcome(state: str, final_text: str) -> TurnOutcome:
            return TurnOutcome(
                task_id=context.task_id,
                context_id=context.context_id,
                state=state,
                text=final_text,
                usage=dict(usage),
                cost_usd=round(cost_usd, 6),
                duration_ms=int((time.monotonic() - started) * 1000),
                llm_calls=llm_calls,
                tool_calls=tool_calls,
                models=list(models),
            )

        try:
            async for event_type, payload in self._stream_factory(
                text, context.context_id, resume=resume, caller_trace=caller_trace,
            ):
                if event_type == "text":
                    accumulated += payload

                elif event_type in ("tool_start", "tool_end"):
                    if event_type == "tool_start":
                        tool_calls += 1
                    part = _tool_call_part(event_type, payload)
                    if part is not None:
                        await updater.update_status(
                            TaskState.TASK_STATE_WORKING,
                            message=updater.new_agent_message([part]),
                        )

                elif event_type == "delta":
                    if isinstance(payload, dict):
                        deltas.append(payload)

                elif event_type == "usage":
                    if isinstance(payload, dict):
                        had_usage = True
                        llm_calls += 1
                        usage["input_tokens"] += int(payload.get("input_tokens", 0) or 0)
                        usage["output_tokens"] += int(payload.get("output_tokens", 0) or 0)
                        usage["cache_read_input_tokens"] += int(payload.get("cache_read_input_tokens", 0) or 0)
                        usage["cache_creation_input_tokens"] += int(payload.get("cache_creation_input_tokens", 0) or 0)
                        cost_usd += float(payload.get("cost_usd", 0.0) or 0.0)
                        model = payload.get("model", "")
                        if model and model not in models:
                            models.append(model)

                elif event_type == "confidence":
                    if isinstance(payload, dict) and payload.get("confidence") is not None:
                        confidence = max(0.0, min(1.0, float(payload["confidence"])))
                        expl = payload.get("explanation")
                        confidence_expl = expl.strip() if isinstance(expl, str) and expl.strip() else None

                elif event_type == "input_required":
                    question = (
                        payload.get("question", "Input required.")
                        if isinstance(payload, dict) else str(payload)
                    )
                    await updater.requires_input(
                        message=updater.new_agent_message([_text_part(question)])
                    )
                    return  # parked — caller resumes via message/send on this task

                elif event_type == "done":
                    final_text = payload or accumulated
                    parts = _terminal_parts(
                        final_text, deltas, usage if had_usage else None,
                        cost_usd, confidence, confidence_expl, success=True,
                    )
                    if parts:
                        await updater.add_artifact(parts, last_chunk=True)
                    await updater.complete()
                    _notify_terminal(_outcome("completed", final_text))
                    return

                elif event_type == "error":
                    await updater.failed(
                        message=updater.new_agent_message([_text_part(str(payload))])
                    )
                    _notify_terminal(_outcome("failed", accumulated))
                    return

            # Stream ended without an explicit terminal event — treat the
            # accumulated text as the answer.
            parts = _terminal_parts(
                accumulated, deltas, usage if had_usage else None,
                cost_usd, confidence, confidence_expl, success=True,
            )
            if parts:
                await updater.add_artifact(parts, last_chunk=True)
            await updater.complete()
            _notify_terminal(_outcome("completed", accumulated))

        except Exception as exc:  # noqa: BLE001 — surface to the task, fail loud
            logger.exception("[a2a] execute crashed for task %s", context.task_id)
            await updater.failed(
                message=updater.new_agent_message([_text_part(str(exc))])
            )
            _notify_terminal(_outcome("failed", accumulated))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_input_required(task: Any) -> bool:
    try:
        return task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    except AttributeError:
        return False


def _extract_caller_trace(context: RequestContext) -> dict:
    """Pull ``a2a.trace`` metadata off the inbound message (Langfuse cross-trace
    propagation), or {} when absent."""
    msg = context.message
    if msg is None:
        return {}
    try:
        meta = json_format.MessageToDict(msg.metadata) if msg.metadata else {}
    except Exception:  # noqa: BLE001
        return {}
    trace = meta.get("a2a.trace")
    return trace if isinstance(trace, dict) else {}


def _tool_call_part(event_type: str, payload: Any) -> Part | None:
    """Build a tool-call-v1 DataPart from a tool_start/tool_end event."""
    if isinstance(payload, dict):
        phase = "started" if event_type == "tool_start" else "completed"
        kwargs: dict[str, Any] = {}
        if event_type == "tool_start" and payload.get("input") is not None:
            kwargs["args"] = payload.get("input")
        if event_type == "tool_end" and payload.get("output") is not None:
            kwargs["result"] = payload.get("output")
        emit = pa.emit_tool_call(
            str(payload.get("id", "")),
            str(payload.get("name", "")),
            phase,
            **kwargs,
        )
        return _ext_data_part(emit)
    if payload:
        return _text_part(str(payload))
    return None


def _terminal_parts(
    text: str,
    deltas: list[dict],
    usage: dict | None,
    cost_usd: float,
    confidence: float | None,
    confidence_expl: str | None,
    *,
    success: bool,
) -> list[Part]:
    """Assemble the terminal artifact's parts: text first, then the cost /
    confidence / worldstate-delta DataParts that have content (order:
    text → worldstate → cost → confidence)."""
    parts: list[Part] = []
    if text:
        parts.append(_text_part(text))
    if deltas:
        parts.append(_ext_data_part(pa.emit_worldstate_delta(deltas)))
    if usage and (usage.get("input_tokens", 0) or usage.get("output_tokens", 0)):
        parts.append(_ext_data_part(pa.emit_cost(
            usage,
            cost_usd=round(cost_usd, 6) if cost_usd > 0 else None,
            success=success,
        )))
    if confidence is not None:
        parts.append(_ext_data_part(pa.emit_confidence(
            confidence, explanation=confidence_expl, success=success,
        )))
    return parts
