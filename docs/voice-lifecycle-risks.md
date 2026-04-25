# Voice lifecycle — risks and dead code

Punch list of latent bugs, doc/code drift, dead wiring, and UX gaps surfaced
during the round-trip audit. Companion file `voice-lifecycle.md` has the
end-to-end spec. Snapshot 2026-04-25.

Each item is sized rough-T-shirt and tagged with a domain so the list can be
sorted by area later. Severity is from a UX/correctness lens, not engineering
effort.

---

## R1. MicroAck doc/code drift on trigger window

**Severity:** low (cosmetic)
**Domain:** filler

`app.py:821` comment says the micro-ack fires "within ~500 ms of UserStoppedSpeaking." The actual default is `trigger_ms: int = 1500` (`agent/micro_ack.py:70`). The bump is documented at `agent/micro_ack.py:65-69` (Ollama-native + small models always won the race and surfaced as "the bot says 'mm' before every reply") — but only at the call site, not at the comment site.

**Fix sketch:** update the `app.py:821` comment to "within ~1.5s" or pull the default into a named constant referenced from both sites.

---

## R2. MicroAck has no Verbosity gate

**Severity:** medium
**Domain:** filler / config

`MicroAckInjector` checks only the boolean `enabled=` toggle; it does not consult `Verbosity.SILENT`. Personas configured `filler_verbosity: silent` still emit hardcoded "mm/hm" acks unless the operator also sets `behavior.micro_ack: false` on the same skill.

By contrast, `BackchannelController` does honor `Verbosity.SILENT` (`backchannel.py:143-144`).

**Fix sketch:** in `MicroAckInjector._fire_after_delay`, gate on `user_state.filler_settings.verbosity != Verbosity.SILENT` before pushing the frame.

---

## R3. `_progress_loop` is dead code today

**Severity:** low (latent)
**Domain:** filler / tools

The slow-tool progress narration loop (`app.py:891-933`) only fires when `tier is Latency.SLOW and not any_async` (`app.py:950-951`). Today:

- Every decorated tool uses `Latency.FAST` (`agent/tools.py:159-356`).
- `delegate_to` is hand-wired and not registered in `_TOOL_REGISTRY`, so `latency_for` returns `Latency.MEDIUM` (`agent/tools.py:92-97`).

Net: nothing in production currently triggers the loop. The wiring is intact but unexercised.

**Fix sketch:** classify `delegate_to` (or specific delegate names known to be long-running, e.g. research agents) as `Latency.SLOW`, OR document the loop as future-only and gate it behind a feature flag so dead code doesn't drift.

---

## R4. Tool-calling is unsupported on Ollama and MLX adapters

**Severity:** high (silent feature loss)
**Domain:** llm

Both `OllamaLLMService.get_chat_completions` (`voice/llm/ollama.py:119-128`) and `MLXLLMService.get_chat_completions` (`voice/llm/mlx.py:118-123`) emit a one-time-per-instance warning `tools in context but adapter does not yet translate them` and then run content-only. Since `register_tools` always attaches at least the orb-mod tools to the LLM context (`app.py:702-709`, no opt-out), every Ollama/MLX session emits this warning at first turn. Tool calls (`set_variant`, `delegate_to`, etc.) are a hard-no on those adapters today.

**Fix sketch:** translate pipecat's tools schema into Ollama's `/api/chat` `tools` field and MLX's chat-template tool-call convention. Until then, log an obvious warning at session connect (not just first chat completion) so operators know they're running tool-less.

---

## R5. `on_summary_applied` discriminator may not match

**Severity:** medium (silent data loss)
**Domain:** memory

The handler at `app.py:781-792` compares `content != skill.system_prompt` (raw persona prompt) against the first `system` message in `context.messages`. But the actual first system message is the *assembled* `_effective_prompt(skill, …)` output (`app.py:740-746`), which includes the persona prompt PLUS tool_use_block, plan_block, repair_block, personality, neglect, recall_block, etc. — the two strings will never be equal.

If pipecat's summarizer inserts a NEW system message for the rolling summary, the handler picks it up correctly (the new message's content differs from BOTH persona and assembled prompt). If pipecat instead modifies the existing first system message, the handler still picks it up — but the loop body only saves the FIRST non-persona match, which is the unmatched assembled prompt itself.

**Worst case:** rolling summaries silently never persist. Next session opens with no `## MEMORY — rolling summary` block, only `prior_n(3)` from the SQLite session log.

**Fix sketch:** add a unit test that exercises the auto-summarization path and asserts `save_summary` was called with the right content. Also consider switching the discriminator to a structural marker (e.g. summarizer-emitted message has a known prefix or a sentinel field).

---

## R6. `_BID_YES` substring match includes `"what"`

**Severity:** medium (false-positive UX)
**Domain:** delivery

`agent/delivery.py:131` puts `"what"` in `_BID_YES`. Resolution uses substring match. A user who says "what time is it?" while a bid is being held will resolve as accept and flush the entire NEXT_SILENCE queue.

**Fix sketch:** drop `"what"` from `_BID_YES`, or switch from substring to whole-word match (`re.search(r"\b{}\b", text)`). Prefer the latter — token-based matching is a general improvement.

---

## R7. `drain_stashed_deliveries` is non-atomic

**Severity:** low (single-owner mitigates, but still)
**Domain:** delivery / persistence

`agent/session_store.py:111-131` does `read JSON → unlink → return`. If the read succeeds but `unlink` fails (filesystem permission, EBUSY, etc.), the same items return next connect → **double playback**. No detection or de-dup.

Single-owner usage makes this practical-safe but not formally safe.

**Fix sketch:** rename the file (atomic on POSIX) before returning, or add a "drained" marker to each item. For real safety, move to SQLite with a transactional `SELECT ... DELETE`.

---

## R8. `stash_delivery` is non-locked read-modify-write

**Severity:** low (single-owner mitigates)
**Domain:** delivery / persistence

Same file (`session_store.py:86-108`). Concurrent disconnects + drains could clobber. Practical-safe today.

**Fix sketch:** combine with R7 — if the persistence layer becomes SQLite, both go away.

---

## R9. `_bid_issued` not persisted across reconnect

**Severity:** low
**Domain:** delivery

`DeliveryController.snapshot_pending()` (`agent/delivery.py:175-187`) emits `phrase / policy / priority / keywords / enqueued_at` but not `_bid_issued`. A reconnect mid-bid loses the held state and re-evaluates fresh — could re-bid, or could drain immediately.

**Fix sketch:** include `bid_issued: bool` (and the bid phrase itself if you want to re-emit) in the snapshot, restore in `replay_stashed`.

---

## R10. WebRTC connect errors are console-only

**Severity:** medium (UX dead end)
**Domain:** frontend

`OrbStage.tsx:53-55` wraps `client.connect()` and `client.disconnect()` in promise chains that only `console.error('[orb] connect error:', err)`. There's no UI surface — the user double-clicks, nothing visible happens, and they have no way to know whether the backend is offline, the API key is wrong, or the SDP handshake timed out.

**Fix sketch:** route the caught error into the StatusPill error transient (the same path `Error` RTVI events use), or surface a dedicated error banner. Bonus: detect 401 (bad API key) and link to settings.

---

## R11. No "connecting" state in StatusPill

**Severity:** medium (UX dead air)
**Domain:** frontend

Between double-click and `BotReady`, the orb sits idle and the StatusPill renders `null` (`StatusPill.tsx:32-43`). On a slow handshake (cold sidecar, big model load, network blip), this can feel like the click was ignored.

**Fix sketch:** when `transportState ∈ {connecting, connected, ready}` AND no transient is active, render "connecting…" or "warming up…". Pulled directly from `usePipecatClientTransportState()` so no new state plumbing is needed.

---

## R12. RTVI consumer is partial — bot-tts and bot-llm-stopped events are unwired

**Severity:** low (latent feature)
**Domain:** frontend

The server emits `BotTtsStarted` / `BotTtsStopped` / `BotLlmStopped` via `RTVIProcessor`, and `web/src/voice/hooks.ts:55-60` exposes them via `useBotTurnEvents`. No component consumes that hook (grep returns no callers).

The current `VoiceStateBridge.tsx` derives "bot is speaking" from `BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame` directly, which works for the orb shader pulse but doesn't surface the LLM-vs-TTS distinction (e.g. the orb can't show "LLM is responding but TTS hasn't started yet" — useful for slow-TTS warning).

**Fix sketch:** decide whether the granular events are wanted — if yes, wire them; if no, delete the hook so it doesn't drift.

---

## R13. Self-modification tool round-trip is HTTP-only

**Severity:** medium (broken feature)
**Domain:** orb / frontend

The orb-self-modification tools (`set_variant`, `apply_palette`, `adjust_param`, `save_preset`, `recall_preset`) call into the backend via the LLM and persist to `config/orbis.yaml` server-side. The frontend orb store reads the config once at plugin load (`web/src/plugins/orb/index.tsx:13` → `loadOrbOverrides()`) and never re-syncs.

So a user saying "switch to the storm variant" today produces:
- Backend: variant change persisted, spoken confirmation works.
- Frontend: orb continues showing the previous variant until the page is reloaded.

The wizard's `selectStarter` workaround (`SetupWizard.tsx:763-771`) proves the gap exists — it double-writes via direct `setVariant`/`applyPreset` calls in the same gesture.

**Fix sketch:** publish a custom RTVI message (or reuse the data channel) on `set_variant`/`apply_palette`/`adjust_param` server-side; consume on the client to re-call `setVariant`/`applyPreset` in real time. `voice/hooks.ts:37` already exposes `sendClientMessage` (unused) — the inverse direction is what's needed.

---

## R14. `text_agent` (A2A inbound) hard-codes env LLM

**Severity:** medium (config inconsistency)
**Domain:** llm / a2a

`text_agent` (`app.py:413-525`) uses module-level `LLM_API_KEY` / `LLM_URL` for the inbound A2A path (`app.py:393-401`), ignoring `persona.llm.url/model/api_key` overrides set in `config/orbis.yaml`.

Result: voice path and inbound A2A path can talk to different LLMs. A user who configures a custom LLM via the wizard will see voice answers from the custom endpoint and A2A answers from the env-default endpoint.

**Fix sketch:** route `text_agent` through the same `make_llm` factory and persona-resolution logic as `run_bot`. Or document the discrepancy if intentional (it doesn't appear to be).

---

## R15. Soft-neglect mood is set, not drifted

**Severity:** info (by design, but worth knowing)
**Domain:** personality

`agent/neglect.py:115-119` *sets* mood targets directly rather than drifting toward them. Comment at `:107-110` documents the rationale: "the shift needs to be visible from turn one."

Trade-off: a single 8-day gap can override slow-drift mood adjustments accumulated over weeks of conversation. The slow-drift system gets steamrolled by the fast neglect system.

**Fix sketch:** if behavior change wanted, switch to a weighted blend (e.g. `set` for first turn after gap, drift back over subsequent turns). Otherwise, no action — just document in `agent/neglect.py` that this is expected.

---

## R16. `audio_out_10ms_chunks=2` semantics for inbound are UNKNOWN

**Severity:** info
**Domain:** transport

`SmallWebRTCTransport(TransportParams(audio_out_10ms_chunks=2, …))` (`app.py:557-567`). The parameter name implies output-only, but the codebase doesn't confirm whether it influences inbound chunking. If it does, it affects VAD windowing precision and STT segment boundaries.

**Fix sketch:** read the pipecat `SmallWebRTCTransport` source to confirm scope. Add a comment at the construction site documenting the answer.

---

## Cross-cutting telemetry gap

`MicroAckInjector` has only an INFO log, no `tracing.span` (compare R3-related `filler.progress`, R12-touched `backchannel.emit`, and `delivery.speak_now`). And no counter for filler/backchannel/micro-ack emission frequency exists in `_METRICS` — only `tool_calls_total/by_name`.

Without these, "is the bot too chatty" or "is filler suppression actually firing on the new persona" can't be answered from telemetry alone.

**Fix sketch:** add `_METRICS["filler_emissions_total"]` plus a per-type breakdown, and a `filler.micro_ack` Langfuse span around the `_fire_after_delay` body. Keeps the four anti-dead-air emitters telemetered uniformly.
