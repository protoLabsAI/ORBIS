# Natural-Sounding Fillers

protoVoice's "thinking out loud" speech — the phrases the agent emits while a tool is running — uses three different mechanisms depending on *when* the speech needs to happen relative to the response stream:

| Channel | When | How |
|:---|:---|:---|
| **Pre-tool preamble** | LLM is about to call a tool | LLM emits it inline, in the same response stream, BEFORE the function call |
| **Progress narration** | A SLOW tool is mid-flight | Separate generator (LLM is blocked on tool, can't narrate) |
| **Backchannel** | User is mid-utterance | Separate generator + timer (LLM isn't producing anything) |

Only the third one needs a fully-separate "filler generator" running in parallel. The first one — the most common case — is just a system-prompt instruction. This is what production agents (OpenAI Realtime, LiveKit, Vapi) all converge on.

## The pre-tool preamble

When the LLM decides to call a tool, the prompt instructs it to first emit a brief acknowledgement (2-12 words depending on verbosity), then make the function call. Pipecat's `OpenAILLMService` streams those acknowledgement tokens to TTS *before* running the function call (see `base_llm.py:457-525`), so the user hears them naturally.

Single LLM call. Single source of truth. Zero race conditions — the preamble and the actual answer are sequential by construction within the same response.

The TOOL USE block we append to every persona's prompt looks like:

```
## TOOL USE — speak BEFORE every tool call

Whenever you call a tool, ALWAYS first emit one short preamble line in
the response, THEN call the tool. The preamble is spoken aloud
immediately so the user knows you heard them and are working.

Preamble length / tone: <verbosity-specific>

CRITICAL — what the preamble must NOT do:
  - never include a fact, name, number, date, or detail from the answer.
  - never restate the user's question.
  - never claim what you found or will find.
The preamble is ONLY about acknowledging that you're checking.

<backend-specific style: Fish gets [softly]/[pause]/[hmm] tags; Kokoro gets plain>
```

See `agent/filler.py::tool_use_block`.

### Why a system-prompt approach beats a parallel generator

The previous design ran a separate LLM call per tool dispatch to generate the filler. Three problems made that unsustainable:

1. **Race conditions.** Tool sometimes returned faster than the filler generator, so the filler arrived AFTER the answer. Sounded like "It's 72 degrees... [3s pause] ...let me check the weather."
2. **Context leakage.** The default `TTSSpeakFrame` appends its text to LLM history. The next response saw its own filler and either echoed it or finished the thought, so the user heard "John, John."
3. **Architectural complexity.** Two LLMs running, two prompts to maintain, plus `append_to_context=False` hacks to keep them from polluting each other.

Inline preamble streaming has none of these problems by design. The model that picks the tool also picks the words; they share context naturally; latency is zero (same call, same stream).

The OpenAI Realtime Prompting Guide prescribes this verbatim: *"Before any tool call, say one short line like 'I'm checking that now.' Then call the tool immediately."* LiveKit's blog and Vapi's docs converge on the same pattern.

## Latency tiers

Each tool registers an expected-latency hint that shapes whether progress narration kicks in:

```
FAST   <500ms tools (calculator, get_datetime)
       Inline preamble speaks; no progress loop. Done in one shot.

MEDIUM 0.5-3s tools (web_search, deep_research, a2a_dispatch)
       Same — preamble + tool + answer, all sequential, no narration.

SLOW   3s+ tools (slow_research, long delegations)
       Preamble + tool dispatch + periodic progress narration via
       FillerGenerator.progress() until result returns.
```

The progress channel can't be inline because the LLM is blocked waiting for the tool result — it can't generate "still looking..." while waiting on its own function call. We synthesize those lines via a separate generator.

## Backchannels

A separate channel for *listener* acks ("mm-hmm", "yeah") fired DURING the user's turn. Implemented by `BackchannelController` (`agent/backchannel.py`) — it watches `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame`, kicks off a timer, and emits one backchannel after `BACKCHANNEL_FIRST_SECS` (default 5s), repeating every `BACKCHANNEL_INTERVAL_SECS` (6s).

These are necessarily separate from the LLM's response stream because the agent isn't speaking a turn at all in this moment — the user is.

## Micro-ack

A tiny acoustic acknowledgement (`mm` / `mhm` / `hm` / `okay`) fired ~500 ms after `UserStoppedSpeakingFrame` **only if** the main pipeline hasn't produced audio yet. Vapi's "Fill Injection" pattern. Implemented by `MicroAckInjector` (`agent/micro_ack.py`). Cancels when `BotStartedSpeakingFrame` fires, so fast turns never hear it. Plays via `TTSSpeakFrame(append_to_context=False)` so it serializes before the main response (no overlap) and never enters LLM context. Fish backend wraps with `[softly]` for a quieter delivery; other backends use plain text.

## Backend-aware rendering

Both the inline preamble (via the prompt's style block) and the generator paths (progress + backchannel) render differently per TTS backend:

- **Fish Audio S2-Pro** — supports 15,000+ inline prosody tags. The prompt block tells the LLM to use `[softly]`, `[pause:Xms]`, `[hmm]`, `[thinking]`, `[whisper]` sparingly, pairing a filler with a ~300 ms pause ([research-backed: fillers alone read fake, fillers + pauses cross the uncanny valley](https://www.sesame.com/research/crossing_the_uncanny_valley_of_voice)). Output looks like `[softly] hmm, [pause:300] let me check`.
- **Kokoro 82M / OpenAI TTS** — no inline tag support; brackets would be spoken as literal syllables. The prompt block forbids brackets and forces plain text. Additionally, each adapter plugs `ProsodyTextFilter` into pipecat's `text_filters=` kwarg, so any tag the LLM emits anyway gets stripped before synthesis.

Regardless of backend, `ProsodyTagStripper` sits between `transport.output()` and `assistant_agg` so tags never leak into LLM conversation history — the context stays clean even though Fish spoke them.

The skill's `tts_backend` field at session-start time decides which style block goes into the prompt.

## Verbosity → prompt variable

`VERBOSITY` (silent / brief / narrated / chatty) is no longer a runtime branch in code — it's a knob on the prompt:

```
silent     → "When you call a tool, do NOT say anything before the call."
brief      → "Preamble length / tone: 2 to 4 words. Casual, low energy."
narrated   → "Preamble length / tone: 4 to 8 words. Warm, natural."
chatty     → "Preamble length / tone: Up to 12 words. Slightly expressive."
```

Snapshotted at session start (same as skill). Changing `VERBOSITY` mid-session via `POST /api/verbosity` affects future sessions, not the live one.

## Sources

- [OpenAI Cookbook — Realtime Prompting Guide](https://cookbook.openai.com/examples/realtime_prompting_guide)
- [LiveKit — Prompting voice agents to sound more realistic](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic)
- [Vapi Prompting Guide](https://docs.vapi.ai/prompting-guide)
- [Sesame — Crossing the uncanny valley of voice](https://www.sesame.com/research/crossing_the_uncanny_valley_of_voice)
- [Fish Audio S2 — Fine-grained voice control at the word level](https://fish.audio/blog/fish-audio-s2-fine-grained-ai-voice-control-at-the-word-level/)
- [Pipecat PR #714 (closed — inline handling preferred)](https://github.com/pipecat-ai/pipecat/pull/714)
- [Pipecat issue #3459 (TTSSpeakFrame context leak)](https://github.com/pipecat-ai/pipecat/issues/3459)
- [Pipecat issue #1959 (double-talk with tool_calls)](https://github.com/pipecat-ai/pipecat/issues/1959)
- [LiveKit issue #3030 (closed not-planned — filler belongs in prompt)](https://github.com/livekit/agents/issues/3030)
