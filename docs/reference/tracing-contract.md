# Cross-Fleet Tracing Contract

This document specifies the HTTP header contract protoVoice uses to stitch Langfuse traces across the protoLabs agent fleet. Workstacean, ava, and any future fleet agents implement against this so a full user-turn trace spans every service it touches, not just ours.

## TL;DR

On every outbound request to another agent, protoVoice attaches two headers:

| Header | Required | Shape | Purpose |
|:---|:---:|:---|:---|
| `Langfuse-Session-Id` | yes | 32-char hex | The caller's Langfuse session (= our WebRTC session) |
| `Langfuse-Trace-Id` | yes | 32-char hex | The current user-turn trace ID |
| `Langfuse-Parent-Observation-Id` | optional | 32-char hex | If present, the caller wants your spans nested under this specific observation (span) instead of directly under the trace. |

Receivers **must** honor them: instead of creating a fresh trace, continue the caller's trace with `langfuse.trace(id=trace_id, session_id=session_id)`. All spans you open for this request then nest inside the caller's trace.

If the headers are absent, treat the call as an independent trace — normal Langfuse behaviour.

## When protoVoice attaches these headers

Every outbound HTTP call made in the context of a live user turn:

- **A2A `message/stream` / `message/send`** — `a2a/client.py::dispatch_message_stream` and `dispatch_message`.
- **OpenAI-compat `/v1/chat/completions`** to a delegate — `agent/delegates.py::_dispatch_openai`.
- **Any future fleet-to-fleet RPC** that runs inside a user-turn trace.

The values come from the `_ACTIVE_TRACER.get_current_trace()` in `agent/tracing.py`. If the TurnTracer has no live trace (pre-session or post-session), the headers are omitted.

## What receivers must do

### 1. Accept the headers

```python
# Python (example; any language works)
trace_id = request.headers.get("Langfuse-Trace-Id")
session_id = request.headers.get("Langfuse-Session-Id")
parent_id = request.headers.get("Langfuse-Parent-Observation-Id")
```

### 2. Continue, don't create

```python
if trace_id and session_id:
    trace = langfuse.trace(id=trace_id, session_id=session_id)
    # New spans for this request nest under the caller's trace:
    span = trace.span(name="ava.handle_request", parent_observation_id=parent_id)
else:
    trace = langfuse.trace(name="ava.standalone_request")
```

### 3. Propagate

If your agent itself calls further downstream agents (chained delegation), continue propagating the same headers. The trace fans out as a single tree.

### 4. Flush

Call `langfuse.flush()` before returning the HTTP response so the caller sees your spans in Langfuse within a second or two, not eventually.

## Why session + trace, not just trace

Sessions are Langfuse's unit for "one conversation" — they're what users filter by in the UI. Each WebRTC session is one Langfuse session; every user turn in that session is a trace under it. Including session_id lets the receiver display your spans under the correct conversation in their view even if they also happen to aggregate by session elsewhere.

## Security

These headers are **identifiers, not secrets**. Forging a Langfuse-Trace-Id only lets you append spans to someone else's trace in the Langfuse UI — it doesn't grant access to anything. There's no authentication implied; agent-to-agent authentication happens separately (API keys, bearer tokens, the existing A2A auth model).

Don't log the full header values in high-volume places — they're noisy but otherwise benign.

## Versioning

This contract is v1. Future changes (e.g. W3C traceparent interop) bump the version via an additional `Langfuse-Contract-Version: 2` header; receivers fall back to v1 behaviour when unset.

## Implementation status

- **protoVoice** — writing these headers: K24 (this release). Reading them (for inbound requests via `/a2a`, future `/api/*`): K24 receive side.
- **workstacean** — ava's side: workstacean team implements against this doc.
- **Any future fleet agent** — same.
