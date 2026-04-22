# Duplex Design

Standard voice agents are **half-duplex**: user speaks, agent responds, turns alternate. protoVoice aims to be **full-duplex** in two specific senses.

## Speak-while-thinking

When the agent calls a tool that takes several seconds, normal agents go silent. Users interpret silence as "it didn't hear me" and often re-ask. Bad.

protoVoice emits a short filler phrase the moment a tool dispatches — "Hold on.", "Let me look that up." — and, if the tool drags on, keeps narrating progress: "Still looking.", "Almost there."

Mechanism:

1. LLM emits a tool call. Pipecat fires `on_function_calls_started`.
2. Our handler queues `TTSSpeakFrame(<opening_phrase>)` on the pipeline task. This is a special frame that Pipecat synthesizes and plays *immediately*, independent of the current LLM turn.
3. A background `_progress_loop()` coroutine fires a **two-tier cadence** (Alexa pattern): first ack after `progress_first_secs` (~2 s), second ack after an additional `progress_second_secs` (~6 s). Then stops — over-narrating past ~8 s reads as performative. Cancelled on tool completion or barge-in.
4. The tool handler is wrapped: on return (success or failure) it cancels the progress loop.

The opening phrase is gated on verbosity. `silent` emits nothing. `brief` emits a short filler. `narrated` and `chatty` emit progressively more.

## Push-interrupt

For long-running tools — an A2A call to another agent, a multi-source web search — the agent shouldn't block the entire turn. Users want to keep talking.

The duplex solution: register the tool with `cancel_on_interruption=False`. Pipecat treats it as async. The LLM returns control immediately — the user can chat about other things. When the tool completes, its result is injected back into the conversation as a "developer" message and the agent speaks the result at the next opportunity (or barges in, if the delivery policy says so).

Pipecat's native async tool path handles the plumbing. Our `TaskInbox` concept from the original design is redundant — pipecat already implements it.

The delivery-policy matrix governs when the injected result is actually spoken:

- **`now`** — push immediately, interrupting the user if necessary. Reserved for urgent results the user is actively waiting for (mapped from `Priority.CRITICAL`).
- **`next_silence`** — wait for VAD-detected silence + 600 ms settle, then speak (from `Priority.TIME_SENSITIVE`). A fallback timer drains anyway if the user stays muted past `DELIVERY_NEXT_SILENCE_FALLBACK_SECS`.
- **`when_asked`** — only speak if the user's next utterance references one of the query's keywords (from `Priority.ACTIVE`). Items past TTL (`DELIVERY_WHEN_ASKED_TTL_SECS`, default 10 min) are dropped silently.

See [Delivery Policies](/guides/delivery-policies) for the full Priority → policy mapping, bid-then-drain, and cross-session replay behaviour.

## Why this isn't a race

Two things could collide:

- **Filler and the real answer overlap.** Pipecat serializes `TTSSpeakFrame`s in queue order. Filler goes first, real response second. If the real response arrives mid-filler, pipecat's text aggregator holds it until the filler finishes speaking.
- **Push and barge-in collide.** If a push result arrives while the user is speaking, VAD fires `UserStartedSpeakingFrame` and broadcasts interruption. `next_silence` items wait for the user to finish + settle delay; `now`-priority items emit anyway. An `BargeInGate` processor applies adaptive filtering on the user side so coughs / backchannels don't count as real interruptions.

## Barge-in vs tool cancellation

By default tools register with `cancel_on_interruption=True` — the user speaking mid-tool cancels the running tool. Good for synchronous "let me check that" flows. Bad for "kicked off a 2-minute research task in the background" flows.

Each tool declares its own `cancel_on_interruption` at registration time (see `agent/tools.py::register_tools`). Fast tools stay cancellable; `slow_research` and any other async tool opt out so they keep running in the background.

## Filler UX gotchas

- Fillers overlapping trivial <2 s responses is jarring. Solve by skipping the opening filler if the LLM's own response is expected to start within 1 s (tunable).
- Repeating the same phrase loop ("one sec... one sec... one sec") is grating. Phrase pool + randomization; eventually we should track recent phrases per session to avoid repeats.
- User interrupts the filler. Current behaviour: VAD fires, interruption broadcast, filler cuts off, user turn takes over. Good default.
