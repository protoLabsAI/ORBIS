# Voice lifecycle — research integration plan

Companion to `voice-lifecycle.md` and `voice-lifecycle-risks.md`. Bridges the
ORBIS engineering reality (what's actually in the pipeline today) to the
companion-stack research program (small specialized models being trained
under [`protoLabsAI/protoLab/experiments/companion-stack`](https://github.com/protoLabsAI/protoLab/tree/main/experiments/companion-stack))
so research can attach at the right pipes, with the right contracts, against
the right metrics.

Snapshot 2026-04-25. The companion-stack roadmap currently has Phase 0 done,
Phase 1 in flight as ORBIS issue [#35](https://github.com/protoLabsAI/ORBIS/issues/35).

## The framing

The companion-stack thesis: **LLMs are good reasoning engines and bad
everything else** — bad perception, bad routing, bad signal detection, bad
on the hot path. Voice companion responsiveness comes from a stack of small
specialized models at every pipe of the loop, doing what they're cheap and
predictable at, while the LLM handles language and reasoning.

The lifecycle audit confirms ORBIS today fits the "LLM-only" pattern almost
everywhere off the audio path:

| Pipe | What lives here ideally | ORBIS today |
|---|---|---|
| **audio-pre** | tags / VAD / speaker ID / events | Silero VAD + Whisper STT (`app.py:752`, `voice/stt.py`) |
| **text-pre** | intent / topic / NER / sentiment | none — LLM decides everything |
| **llm-context** | embeddings / rerank / tool-need | naive `prior_n(3)` (`app.py:180-225`) |
| **text-post** | prosody / safety / style | tag stripper only (`agent/prosody.py`) |
| **memory** | fact-worthy / coreference / decay | LLM-driven curator + 90-day half-life (`memory/facts.py`) |
| **visual** | mood→palette / animation | manual `apply_palette` tool calls (`agent/tools.py:189-216`) |

Research fills these slots. This doc gives each pipe its concrete ORBIS
attachment point — the frame type to subscribe to, the state to read/write,
the existing telemetry it can emit through, and the risk it closes from
`voice-lifecycle-risks.md`.

---

## Pipe-by-pipe attachment points

### audio-pre — perception layer

**Pipeline placement.** Today the head of the pipeline is:

```
transport.input() -> EchoGuardSuppressor -> RTVIProcessor -> stt -> user_agg
```
(`app.py:801-812`)

Audio-pre processors slot **between `EchoGuardSuppressor` and `stt`**, not
between `transport.input` and `stt` as the integration sketch suggests —
echo-guarded audio is the right input (it already has bot-self bleed
removed). Two integration shapes are viable:

1. **Tap** — observe `InputAudioRawFrame` inline, fire downstream model
   asynchronously on `UserStoppedSpeakingFrame`, push frames forward
   unchanged. The `AudioTagsTap` pseudocode in
   [INTEGRATION.md](https://github.com/protoLabsAI/protoLab/blob/main/experiments/audio-tags/INTEGRATION.md)
   is exactly this shape — a sibling pattern to `ProsodyTagStripper`.
2. **Gate** — observe + transform: emit a new frame type
   (`OwnerVerifiedFrame` / `StrangerDetectedFrame`) downstream so other
   processors can react. This is what speaker-verification needs.

Both can coexist; speaker-gate fires first so audio-tags can read
`OwnerVerifiedFrame` and decide whether to update the mood writer.

**State surface available.**

- **Read:** `InputAudioRawFrame.audio` (PCM int16), `UserStartedSpeakingFrame`
  / `UserStoppedSpeakingFrame` for windowing, `BotStartedSpeakingFrame` /
  `BotStoppedSpeakingFrame` if you need to ignore the orb's own audio (echo
  guard already handles this — the audio reaching this stage is
  user-only).
- **Write into LLM context:** `_effective_prompt` (`app.py:330-382`)
  composes 9 blocks; an `[user_audio] ...` annotation block slots cleanly
  between `apply_soft_neglect` (block 8) and `_recall_block` (block 9), or
  rides as a per-turn system-message append managed by a new processor.
- **Write into mood:** `mem.personality.set_mood(valence, arousal,
  guardedness)` (`memory/personality.py:167-217`) is already the
  single-row mood writer used by `apply_soft_neglect`. Audio-tags can
  call the same method.
- **Write into facts:** `agent/tools.py` exposes `remember(fact)` style
  hooks; for direct-write use `mem.facts.add(...)`.

**Frame contract proposal.** New frame types live in a sibling to
`agent/echo_guard.py`. Suggested:

```python
@dataclass
class OwnerVerifiedFrame(Frame): score: float
@dataclass
class StrangerDetectedFrame(Frame): score: float; action: str
@dataclass
class AudioTagsFrame(Frame): tags: dict; confidence: dict
```

Downstream consumers attach via `process_frame` matching — the same
pattern `BargeInGate` and `DeliveryController` already use.

**Telemetry hooks already in place.**

- Langfuse span pattern: `tracing.span("stt.whisper", input={...})` at
  `voice/stt.py:113-131`. Audio-tags should emit `tracing.span("audio_tags",
  input={"sr": ..., "duration": ...}, metadata={"model": "v5-soft"})` with
  `output=tags_dict`.
- Counters: `_METRICS` (`app.py`) — add `audio_tags_emissions_total`,
  `speaker_gate_strangers_total`.
- RTVI events: emit a custom RTVI message on `StrangerDetectedFrame` so the
  frontend can show a status pill. The `RTVIProcessor` is wired at
  `app.py:799` and `rtvi.create_rtvi_observer()` (`app.py:870`) is in the
  observer list — adding a custom server-message consumer needs the
  symmetric client subscriber in `web/src/voice/VoiceStateBridge.tsx`.

**Risk this closes.** None directly from the risks doc — this is net-new
capability. But speaker-verification *prevents* a class of risk not yet
listed: any tailnet visitor today can trigger personality drift
(`app.py:1022-1038`), write to the owner's facts table, and see owner
recall. PR 1 of #35 fixes that.

**In-flight work.** ORBIS issue [#35](https://github.com/protoLabsAI/ORBIS/issues/35),
two PRs:

1. **PR 1 — speaker-verification gate.** Off-the-shelf
   `speechbrain/spkrec-ecapa-voxceleb` (~6 M params, ~50 ms CPU /
   ~5 ms GPU). First-run wizard captures owner enrollment, embedding
   cached at `data/voiceprint.npy`. Cosine threshold (default 0.62)
   tunable from drawer UI without restart. Falls back to **owner-trust**
   when the voiceprint file is missing (preserves no-auth single-user
   deployments).
2. **PR 2 — audio-tags side-channel.** [`protoLabsAI/orbis-audio-tags-v5-soft`](https://huggingface.co/protoLabsAI/orbis-audio-tags-v5-soft)
   (8.32 M params, 1.78 ms Blackwell, low-hundreds-ms CPU). Two consumers:
   (a) context-line injection into LLM system prompt with confidence
   threshold 0.65, (b) mood-table writer. Gated on `OwnerVerifiedFrame`
   so guest voices don't pollute owner mood.

**Open architectural questions raised by the lifecycle that #35 doesn't
yet answer:**

- Where does `apply_soft_neglect` (`agent/neglect.py:101-124`) interact with
  audio-tag mood? Today neglect *sets* mood targets directly (R15 in the
  risks doc). If audio-tags also writes mood per-turn, the two systems will
  collide every session-open. Resolution: neglect should set a *baseline*
  in `personality_axes`, not the per-turn `mood` row. Or audio-tags should
  read the neglect-set mood and only drift from it.
- Should the audio-tags tap share Whisper's forward pass? STATUS.md notes
  v5-soft uses a Whisper-tiny encoder — opportunity to share features with
  whatever Whisper variant `voice/stt.py` loads. Default `WHISPER_MODEL=openai/whisper-large-v3-turbo`
  is a different model, so v0 keeps them separate.
- The `[user_audio]` annotation line — should it be an actual system
  message append managed by `_effective_prompt`, or a UserContext custom
  field? The former is implementation-cheap but pollutes context history
  unless rolling-summary-aware; the latter is cleaner but needs Pipecat
  surface that may not exist.

---

### text-pre — routing layer

**Pipeline placement.** Today nothing lives between the user aggregator and
the LLM except `BargeInGate`, `MicroAckInjector`, `BackchannelController`,
`DeliveryController`. None of those route — they all forward to the LLM.

Text-pre processors subscribe to `TranscriptionFrame` (emitted from
`voice/stt.py:134`) and decide whether the LLM call should happen at all.

**State surface available.**

- **Read:** `TranscriptionFrame.text`, `LLMContext.messages` for one-turn
  history, persona / verbosity from the system prompt.
- **Short-circuit the LLM:** the cleanest exit is to consume the
  `TranscriptionFrame`, emit a `TTSSpeakFrame(response, append_to_context=True)`
  directly, and swallow the original frame so it doesn't reach the LLM.
  Pattern is identical to `MicroAckInjector` except the response is
  intent-classified, not a hardcoded "mm".
- **Direct tool dispatch:** for intents like `orb_self_modify`, route to
  the existing tool handlers in `agent/tools.py` without going through the
  LLM's function-calling path. Saves both the LLM call and the function-
  calling round-trip.

**Risks this closes.**

- **R4 (tools unsupported on Ollama/MLX).** A `tool-need-predictor` plus
  intent classifier means: when the model is Ollama/MLX, only route to the
  LLM the turns that are actual conversation; for tool-needing turns,
  dispatch via direct tool handlers. Turns R4 from "silent feature loss"
  into "tool-needing turns explicitly bypass the toolless adapter."
- **R3 (`_progress_loop` is dead).** A topic-router that classifies "this
  is going to a long-running research delegate" can mark the dispatch as
  `Latency.SLOW`, which finally exercises the progress narration path.

**Telemetry.** Add `intent_classifications_total` counter keyed by class;
Langfuse span `intent_classifier.predict` with input/output. Compare LLM
call count before/after — Phase 2 exit criteria target 30%+ turns skipping
the LLM.

**In-flight work.** None — Phase 2, ~2-3 weeks out per
[ROADMAP.md](https://github.com/protoLabsAI/protoLab/blob/main/experiments/companion-stack/ROADMAP.md).

---

### llm-context — retrieval + tool-need

**Pipeline placement.** Indirect — these run during system prompt assembly
in `_effective_prompt` (`app.py:330-382`) and during tool registration in
`register_tools` (`agent/tools.py:540-565`).

**Reranker attachment.** `_recall_block(user_id)` (`app.py:180-225`) is a
naive `prior_n(3)` ORDER BY `ended_at DESC` — it always returns the last 3
sessions regardless of relevance to the current turn. Replacing this with
retrieve-top-50-then-rerank-to-top-5 is the obvious upgrade:

1. Retrieval: existing Qwen3-Embedding (mentioned in roadmap) over
   `sessions.final_output` and `facts` content, returning top-50 candidates.
2. Cross-encoder rerank: `cross-encoder/ms-marco-MiniLM-L-6-v2` baseline,
   fine-tuned on ORBIS memory pairs.
3. Inject top-5 into `<prior_sessions>` XML block.

But there's a chicken-and-egg: at `_recall_block` time we don't yet have
the user's current turn text — the system prompt is built before the user
speaks. Retrieval needs to be deferred to per-turn or moved to a "just
produced TranscriptionFrame, now refresh recall" pattern. Worth a design
discussion before training the reranker.

**Tool-need predictor.** Binary classifier: "does this turn need any tools
at all?" Today `register_tools` always attaches the full tool surface
(`app.py:702-709`), and the LLM decides per-call whether to invoke. A
predictor moves that decision earlier: if "no tools needed", strip the
`tools` field from the LLM call entirely. Saves both prompt tokens and the
function-call format-compliance overhead.

**Risks this closes.**

- **R4 (tools unsupported on Ollama/MLX).** A tool-need predictor that
  returns "no" for ~80% of turns means the warning at `voice/llm/ollama.py:119-128`
  fires far less often. Combined with a backend-aware override (Ollama/MLX
  always returns "no" until the adapters translate tools), this becomes
  the right shape.
- **R5 (`on_summary_applied` discriminator).** Not directly closed, but a
  reranker-driven recall block sidesteps the summary-vs-prior_n confusion
  by giving us a second signal — relevant past content surfaces via
  reranker even if the rolling summary is missing.

**Telemetry.** Spans `reranker.score`, `tool_need.predict`. Add metric
`llm_calls_skipped_no_tools_total` and compare against
`tool_calls_total`.

---

### text-post — prosody, safety, style

**Pipeline placement.** Today nothing lives between LLM and TTS:

```
... -> llm -> tts -> transport.output() -> ProsodyTagStripper -> assistant_agg
```

A prosody-tagger slots **between `llm` and `tts`** as a `FrameProcessor`
that consumes `TextFrame`s emitted by the LLM and rewrites them with
inline tags before TTS sees them.

**State surface available.**

- **Read:** `TextFrame.text`, current `mood` from `mem.personality.get_mood()`,
  current voice state (idle/listening/thinking/speaking) from the orb store
  (would need a server-side mirror — currently only the frontend tracks it).
- **Write:** push the same `TextFrame` with tags inserted. Fish consumes
  the tags natively (`voice/tts/fish.py`); non-Fish backends apply
  `text_filters=[ProsodyTextFilter()]` (`voice/tts/kokoro.py:51`,
  `voice/tts/openai.py:49`, `voice/tts/elevenlabs.py:61`) which strips them
  before synthesis.

**Tension.** Inserting tags only helps Fish today. For Kokoro / OpenAI /
ElevenLabs the tags get stripped before they reach the synthesizer. Two
ways forward:

1. **Backend-conditional tagging** — only emit tags for Fish; emit nothing
   for the other backends. The prosody-tagger reads `tts_backend` from the
   skill config and turns into a no-op for non-Fish.
2. **Provider-specific prosody rendering** — for OpenAI's `gpt-4o-mini-tts`
   (which understands instruction prompts), translate `[softly]` into a
   prepended instruction prompt. For ElevenLabs, use voice-settings-style
   parameters. Different output API per backend.

Path 2 is much more research-shaped and probably the right Phase 3 target.

**Risks this closes.**

- **R12 (RTVI consumer is partial).** Indirectly — if prosody-tagger emits
  a custom RTVI message on every tag insertion, the frontend orb shader
  has live mood signal beyond what `useAudioEnvelopes` extracts from the
  bot audio.

---

### memory — fact-worthiness, coreference, curator

**Pipeline placement.** Async, post-turn. Today this is in
`on_client_disconnected` (`app.py:981-1049`):

1. Walk `context.messages` → `mem.sessions.add(...)` to SQLite.
2. Background task `_run_drift_analysis` calls `analyze_session_drift`
   (`agent/personality.py:145-249`) — bounded LLM call returning JSON
   `{deltas: [...]}`.

**Fact-worthiness classifier attachment.** Replace the (likely planned)
LLM-driven curator with a per-turn classifier that runs after the
assistant aggregator commits each turn. Two write-points:

1. After every assistant response: classify the turn as
   `(fact-producing, durable, ephemeral)`. Durable facts go to
   `mem.facts.add(...)` with `confidence` from the classifier.
2. End-of-session: re-run on the full transcript to catch facts the
   per-turn classifier missed.

The 90-day half-life decay (`memory/facts.py:178-237`, prune <0.2) already
handles ephemeral noise — the classifier just needs to keep precision
high enough that decay doesn't have to do all the work.

**Coreference resolver.** Spacy / fastcoref baseline + ORBIS-specific
fine-tune on the entity registry. Resolves "she said yesterday" to
specific facts row at recall time, before the recall block hits the LLM.

**Risks this closes.**

- **R5 (`on_summary_applied` discriminator).** Not directly, but if the
  curator is doing the heavy lifting, the rolling summary becomes less
  load-bearing — even if pipecat's auto-summarizer never persists, the
  fact table still has the durable signal. Defense in depth.

---

### visual — orb expression

**Pipeline placement.** Today the orb visualization listens to RTVI events
on the frontend (`web/src/voice/VoiceStateBridge.tsx:30-74`) and the bot
audio track (`OrbStage.tsx:25-37` via `usePipecatClientMediaTrack`).

A mood-to-palette driver is server-side (where the audio-tags mood lives)
but its effect needs to round-trip to the frontend. The current
self-modification round-trip is **broken at runtime** (R13): tool calls
write `config/orbis.yaml` but the running client only re-reads on next
page load.

**Risks this closes.**

- **R13 (self-modification doesn't take effect at runtime).** Mood-to-
  palette is the forcing function — if the orb's color is supposed to
  shift continuously with audio-tag mood, it can't wait for a page reload.
  Either:
  1. Add a custom RTVI message channel for orb config updates
     (`rtvi.send_custom_message(...)` server-side, consumed in
     `VoiceStateBridge.tsx`), or
  2. Have the orb subscribe to `/api/config` changes via an SSE stream.

  Option 1 is cleaner — RTVI is already wired both ways. Closes R13 as a
  prerequisite for any visual research that requires real-time updates.

- **R12 (bot-tts events unconsumed).** A `speaking-state` driver in
  Phase 4 needs `BotTtsStarted` / `BotTtsStopped` to drive
  preparing/speaking-quiet/speaking-loud transitions distinct from
  `BotStarted/StoppedSpeakingFrame`. Closing R12 unblocks this.

---

## Risk → research crosswalk

For the punch list in `voice-lifecycle-risks.md`, here's which research
thread (if any) closes each:

| Risk | Closed by | Phase |
|---|---|---|
| R1 — MicroAck doc/code drift | engineering fix, not research | — |
| R2 — MicroAck no Verbosity gate | engineering fix | — |
| R3 — `_progress_loop` is dead code | text-pre topic-router (mark long delegates SLOW) | 2 |
| R4 — Tools unsupported on Ollama/MLX | llm-context tool-need predictor (turns silent failure into explicit gating) + adapter fix | 2 + eng |
| R5 — `on_summary_applied` discriminator | memory fact-worthiness (defense in depth) | 3 |
| R6 — `_BID_YES` includes `"what"` | engineering fix | — |
| R7 — `drain_stashed_deliveries` non-atomic | engineering fix (move to SQLite) | — |
| R8 — `stash_delivery` non-locked | engineering fix (move to SQLite) | — |
| R9 — `_bid_issued` not persisted | engineering fix | — |
| R10 — WebRTC connect errors console-only | engineering fix | — |
| R11 — No "connecting" state in StatusPill | engineering fix | — |
| R12 — RTVI consumer partial | text-post + visual (forces consumption) | 3-4 |
| R13 — Self-modification round-trip broken | visual mood-to-palette (forcing function) | 4 |
| R14 — `text_agent` hard-codes env LLM | engineering fix | — |
| R15 — Soft-neglect mood is set, not drifted | audio-tags interaction (forces reconciliation) | 1 |
| R16 — `audio_out_10ms_chunks=2` semantics | docs read | — |
| Telemetry gap (MicroAck no span) | engineering fix | — |

Most of the punch list is plain engineering. A handful become research-
adjacent — the research either creates the pressure to fix them
(audio-tags + mood reconciliation, mood-to-palette + R13) or makes them
moot (tool-need predictor + R4).

---

## Eval substrate

The companion-stack methodology requires (a) majority/linear-probe
baselines, (b) one off-the-shelf comparable model, (c) held-out sets that
match ORBIS's actual conditions. Two ORBIS-side things support this:

### Existing telemetry that becomes the "actual conditions" eval

- **Langfuse spans** (`stt.whisper`, `backchannel.emit`, `filler.progress`,
  `delivery.speak_now`, `tracing.span` calls throughout). Every session is
  already traced. Spans carry input/output, so they're harvestable as
  training/eval data with the user's consent.
- **`_METRICS` counters** (`app.py`): `sessions_total`, `sessions_active`,
  `tool_calls_total`, `tool_calls_by_name`. Per-turn rates derivable.
- **First-audio-out budget** (~1.0–1.2s, see `voice-lifecycle.md` § Audio
  out). Any new processor that pushes this above ~1.5s should fail eval.
  Per the audio-tags integration sketch, the v5-soft model's <50 ms
  budget is comfortably within margin.
- **Session SQLite** — `prior_n(3)` and `final_output` per session
  (`memory/sessions.py:82-90`). This is the recall-quality eval substrate
  for Phase 2 reranker work.

### Eval gaps the research should fill

- **No user-perceived-latency benchmark exists.** STATUS.md cites
  components (TTFB, TTFA) but not end-to-end user-felt latency under
  realistic load. Phase 5's "End-to-end latency profiler" should land
  here first, before the heavier classifiers go in.
- **No fact-recall held-out set.** Phase 3 can't measure whether ORBIS
  "remembers" without one. Prerequisite for Phase 3's exit criteria.
- **No prosody-quality A/B framework.** Comparing tagged-Fish vs
  bare-Kokoro on the same response needs a listening test infra. Phase 3
  prosody-tagger work should bring this up.

### Personalization fine-tune flow

Phase 5 generalizes the audio-tags Gradio personalization app
([`experiments/audio-tags/app.py`](https://github.com/protoLabsAI/protoLab/blob/main/experiments/audio-tags/app.py))
to other heads. ORBIS-side this needs:

- A first-run / drawer flow that captures user-recorded samples for the
  active head.
- A `personalized.ckpt` swap point in each FrameProcessor (the audio-tags
  config in #35 already supports this shape).
- Per-user model storage — `data/personalized/{user_id}/{head}.ckpt`.
  Single-owner today, but the path is keyed for future multi-user.

---

## Sequencing — what should happen when

Putting Phase 1 (in flight) and Phase 2 (next, ~2-3 weeks) against the
ORBIS lifecycle gives this rough order:

1. **#35 PR 1: speaker-verification gate** — lands first, prereq for
   PR 2's owner-only mood writes.
2. **#35 PR 2: audio-tags side-channel** — lands second; closes the
   audio-pre slot.
3. **Resolve R15 (soft-neglect vs audio-tags mood collision)** — small
   refactor to `agent/neglect.py`; required so Phase 1's mood writes
   don't fight `apply_soft_neglect`'s session-open writes.
4. **Phase 2 begins** — text-pre intent classifier and llm-context
   tool-need predictor in parallel. Both directly mitigate R4 (Ollama/MLX
   tool gap).
5. **Phase 2 reranker** — needs a per-turn recall refresh point added to
   `_effective_prompt` first; this is engineering scaffolding, not research.
6. **Engineering cleanup pass** on R10 / R11 / R7-R9 / R6 / R14 — these
   are independent of research and should land whenever.
7. **Phase 3 prosody-tagger** — depends on text-post placement decision
   (Path 1 vs Path 2 above) being made.
8. **Phase 4 visual work** — depends on R13 being resolved first
   (server→client config update channel). Engineering ticket.
9. **Phase 5 latency profiler** — overdue; should ideally happen
   alongside Phase 1 so we have a clean before/after on the perception
   layer's cost.

The cleanest "what could research/eng do this week if it had to pick one"
answer: **resolve R15 in eng, finish #35 PR 1 in research/eng**. After
that, the audio-pre slot is filled and Phase 2 has a clean starting line.

---

## Open architectural questions (not yet framed by research)

Things the lifecycle audit surfaced that the companion-stack roadmap
doesn't currently address:

1. **Audio-tag temporal aggregation.** Mood, voice quality, environment
   change at different timescales. Per-turn audio-tags writes to mood may
   be too noisy for the slow-drift `personality_axes`. Does the mood
   table need a smoothing window (last-N-turns EWMA)? Phase 3
   `mood-summarizer` is in the backlog but not yet shaped.
2. **Speaker-gate during VAD ambiguity.** What happens when the
   verification score sits in the middle range (0.5-0.7 with threshold
   0.62)? Refuse, warn, ask? The speaker-verification spec doesn't yet
   cover boundary cases.
3. **Tool-need predictor in a multi-tool world.** "Does this turn need
   any tools" is a low-bar binary. Tool-needing turns then need a
   second classifier to pick *which* tools to expose. That's a topic-
   router/tool-router problem, not yet scoped.
4. **Text-post tagging is multi-target.** Fish wants brackets, OpenAI
   wants instruction prompts, ElevenLabs wants parameters. The text-post
   slot is really three separate adapters. The roadmap names one.
5. **Visual continuous expression vs discrete state.** The orb today
   crossfades between discrete states (`STATE_XFADE_MS=600`). Audio-tag-
   driven *continuous* mood expression needs either a much shorter
   crossfade window or a different shader-driving primitive. Phase 4
   should confirm the visual primitive before training the mood-to-
   palette classifier.
6. **Per-user models in a single-owner system.** The single-owner
   posture (DECISIONS.md) implies one set of personalized weights. But
   the speaker-verification gate enables guest sessions — should those
   use a generic head, owner's head, or a per-guest head? Probably
   generic; worth deciding before Phase 5's personalization flow lands.

These don't block research; they're notes for whoever shapes the
relevant experiment's PLAN.md.
