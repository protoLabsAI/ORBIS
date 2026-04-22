# Tracing

protoVoice emits Langfuse traces covering every user turn end-to-end. When paired with instrumented fleet peers, a single trace spans the entire conversation — from mic → STT → router LLM → delegated agent → TTS → speaker — regardless of how many services are involved.

## The trace model

- **Session** = one WebRTC session. `session_id` is assigned at `on_client_connected` and persists until disconnect. Langfuse UI groups traces by session so one conversation renders as a unified timeline.
- **Trace** = one user turn. Opens on `UserStoppedSpeakingFrame`, closes when both the LLM response ends (`LLMFullResponseEndFrame`) and the bot audio stops playing (`BotStoppedSpeakingFrame`). The trace's `input` field carries the transcribed user utterance.
- **Spans** = individual pipeline stages, nested under the trace:

```
user_turn                                     (trace)
├── stt.whisper         (manual span)
├── llm.response        (frame-bounded span)
│   └── generation      (langfuse.openai auto-span per API call)
├── tool.calculator     (frame-bounded span, if invoked)
├── tool.delegate_to    (frame-bounded span, if invoked)
│   └── …downstream agent's spans (if Langfuse-instrumented fleet peer — see tracing-contract)
├── filler.progress     (manual span — per progress tier)
├── backchannel.emit    (manual span — per ack)
├── delivery.speak_now  (manual span — per out-of-band push)
└── tts.fish            (manual span — or tts.kokoro / tts.openai)
```

## Wiring

### Pipeline-observer: `agent/tracing.TurnTracer`

Registered as a `BaseObserver` on the PipelineTask. Watches the frame stream and opens/closes the **trace** + the frame-bounded spans (`llm.response`, `tool.{name}`). Nothing in the application code has to touch the trace directly for those boundaries.

### Module-level registry

Other modules (tool handlers, delegate dispatch, A2A server) reach the live trace via:

```python
from agent import tracing

with tracing.span("my.operation", input={...}) as sp:
    result = do_work()
    sp.update(output=result)
```

`tracing.active_trace()` returns the current `user_turn` trace, or a `_NullSpan` when no turn is live. `tracing.span(name, **kwargs)` is a context-manager shortcut that opens/closes a span on the active trace.

Every span is **automatically stamped** with `user_id` + `session_id` read from ContextVars set at the top of each voice / A2A turn. Filter your Langfuse traces by user or session without threading ids through every call site. The active-tracer registry itself is per-user (`dict[user_id, tracer]`) so concurrent sessions keep their traces isolated.

### Automatic LLM tracing

`FillerGenerator` imports `AsyncOpenAI` from `langfuse.openai` when `LANGFUSE_*` env is set; every filler / backchannel / progress LLM call becomes a generation span automatically, with prompt + completion + token usage captured.

The main conversational LLM (OpenAILLMService → vLLM Qwen) isn't wrapped by `langfuse.openai` because pipecat builds its own client. The `llm.response` frame-bounded span captures its boundary; finer token-level metrics land via pipecat's built-in metrics observer.

## Turning tracing on

Three env vars, all required to enable:

```bash
LANGFUSE_HOST=http://ava:3000        # or your self-hosted / cloud URL
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

If any are missing, every helper is a no-op — local dev without Langfuse keeps working. See [Environment Variables → Tracing](./environment-variables#tracing-langfuse).

## Cross-fleet propagation

Every outbound request to another agent attaches the current trace context via headers:

- `Langfuse-Session-Id`
- `Langfuse-Trace-Id`
- `Langfuse-Parent-Observation-Id` (optional, for explicit span nesting)

Receiving agents continue the trace by calling `langfuse.trace(id=..., session_id=...)` instead of starting a new one — all their spans then nest inside ours. Full spec: [Tracing Contract](./tracing-contract).

When an inbound `/a2a` request includes those headers, protoVoice honors them the same way — the text agent's spans nest under the caller's trace.

## Viewing a trace

Once tracing is on and someone completes a user turn:

1. Open Langfuse → your protoVoice project → **Traces**.
2. Filter by `session_id` to isolate a single conversation.
3. Open a `user_turn` trace to see the full pipeline timeline with per-span latency, input/output, and errors.

Typical inspection flow for a latency regression:

- **STT slow?** `stt.whisper` span latency.
- **LLM slow?** `llm.response` span + any nested generation spans for token-level insight.
- **Tool slow?** `tool.{name}` span shows how long calculator / web_search / delegate_to took.
- **Delegated work slow?** Nested agent spans from the delegate (if they speak the contract).
- **TTS slow?** `tts.fish` / `tts.kokoro` span.
- **Overall voice-to-voice?** Trace wall-clock from open to close.

## Turning tracing off

Set any of `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` to empty (or unset). Next restart picks up the no-op path with zero overhead.
