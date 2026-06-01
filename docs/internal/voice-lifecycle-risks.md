# Voice lifecycle — risks and dead code

Punch list of latent bugs, doc/code drift, dead wiring, and UX gaps surfaced
during the round-trip audit. Companion file `voice-lifecycle.md` has the
end-to-end spec.

Each item is sized rough-T-shirt and tagged with a domain so the list can be
sorted by area later. Severity is from a UX/correctness lens, not engineering
effort.

Snapshot 2026-04-26 (main at v0.1.32). 9 of 16 items resolved by the
PR stack below; details inline under each item plus a quick-reference
table at the top.

---

## Resolved

| Risk | PR(s) | Released | Notes |
|---|---|---|---|
| **R1** — MicroAck doc/code drift | (Already correct on main; the audit captured stale state) | — | No code change required |
| **R2** — MicroAck no Verbosity gate | [#42](https://github.com/protoLabsAI/ORBIS/pull/42) | v0.1.18 | Live `verbosity_getter` callable; SILENT suppresses emit. Plus telemetry: new `filler.micro_ack` Langfuse span |
| **R5** — `on_summary_applied` discriminator | [#46](https://github.com/protoLabsAI/ORBIS/pull/46) | v0.1.17 | **Was worse than the audit said** — pipecat injects summary as a *user* message at index 1; old handler matched the assembled persona prompt and silently saved THAT as the "summary" every session. Fix: per-session UUID-nonce-scoped `<orbis-summary-{nonce}>` tags so user content can't trigger persistence (prompt-injection-resistant per CR Major) |
| **R6** — `_BID_YES` includes `"what"` | [#41](https://github.com/protoLabsAI/ORBIS/pull/41) | v0.1.??? | Drop bare `"what"` + word-boundary regex |
| **R7+R8** — Non-atomic stash/drain | [#47](https://github.com/protoLabsAI/ORBIS/pull/47) | — | Atomic-rename drain + fcntl-locked stash + crash-recovery for stale `.draining` files |
| **R9** — `_bid_issued` not persisted | [#43](https://github.com/protoLabsAI/ORBIS/pull/43) | v0.1.20 | `bid_issued` flag stamped on each snapshot item; replay restores |
| **R10+R11** — WebRTC connect errors / no connecting state | [#49](https://github.com/protoLabsAI/ORBIS/pull/49) | — | New `pushStatusTransient` store; OrbStage surfaces connect errors; StatusPill shows "connecting…" during handshake |
| **R14** — `text_agent` hard-codes env LLM | [#44](https://github.com/protoLabsAI/ORBIS/pull/44) | — | New `_resolve_skill_llm` shared by `run_bot` + `text_agent` |
| **R15** — Soft-neglect mood overwrites drift | [#60](https://github.com/protoLabsAI/ORBIS/pull/60) + [#70](https://github.com/protoLabsAI/ORBIS/pull/70) + [#73](https://github.com/protoLabsAI/ORBIS/pull/73) | v0.1.25 / v0.1.28 / v0.1.30 | Three-writer mood pattern: `set_mood` (operator), `drift_mood_toward` (session-open neglect), `drift_mood` (per-turn audio-tags). All three compose; no overwrites |

Plus the **cross-cutting telemetry gap** (no Langfuse span on MicroAck) was closed with R2 in [#42](https://github.com/protoLabsAI/ORBIS/pull/42).

Still open: R3, R4, R12, R13, R16. Details below.

---

## R1. ✅ RESOLVED — MicroAck doc/code drift on trigger window

**Severity:** low (cosmetic)
**Domain:** filler
**Status:** No code change required — the `app.py` comment already read "~1500 ms" in the live tree at audit time. The audit captured stale state.

---

## R2. ✅ RESOLVED — MicroAck has no Verbosity gate

**Severity:** medium
**Domain:** filler / config
**Closed by:** [#42](https://github.com/protoLabsAI/ORBIS/pull/42) (v0.1.18). `MicroAckInjector` now takes a `verbosity_getter: Callable[[], Verbosity] | None` that's checked at fire time (so a runtime `/api/verbosity` flip is honored on the same turn). `app.py` wires `lambda: user_state.filler_settings.verbosity`. PR also closed the cross-cutting telemetry gap by adding the `filler.micro_ack` Langfuse span. PR #73 (v0.1.30) follow-up: defensive try/except around the getter so a torn-down user_state during shutdown can't crash the timer task.

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

Both `OllamaLLMService.get_chat_completions` (`voice/llm/ollama.py:119-128`) and `MLXLLMService.get_chat_completions` (`voice/llm/mlx.py:118-123`) emit a one-time-per-instance warning `tools in context but adapter does not yet translate them` and then run content-only. Every Ollama/MLX session with delegates configured emits this warning at first turn. Tool calls (`delegate_to`, `adjust_personality`) are a hard-no on those adapters today.

**Fix sketch:** translate pipecat's tools schema into Ollama's `/api/chat` `tools` field and MLX's chat-template tool-call convention. Until then, log an obvious warning at session connect (not just first chat completion) so operators know they're running tool-less.

---

## R5. ✅ RESOLVED — `on_summary_applied` discriminator never matched

**Severity:** ~~medium~~ **HIGH** (silent data corruption — worse than the audit said)
**Domain:** memory
**Closed by:** [#46](https://github.com/protoLabsAI/ORBIS/pull/46) (v0.1.17).

**Worse than the audit thought**: pipecat's summarizer injects the rolling summary as a **user-role** message at `context.messages[1]`, not a system message. The old discriminator walked system messages and saved the *assembled `_effective_prompt`* as the "summary" every single session. Next session's `_recall_block` then loaded the persona prompt back as if it were the previous session's summary — silent prompt-context loop.

**Fix**: pipecat's `summary_message_template` configured to wrap the generated summary in `<orbis-summary-{nonce}>...</orbis-summary-{nonce}>` tags scoped to a per-session UUID nonce (CR's Major call — without the nonce, user content could prompt-inject a fake summary). `_extract_summary_text(messages, open_tag, close_tag)` walks all messages looking for a tagged match. 23 tests including injection-resistance regressions.

---

## R6. ✅ RESOLVED — `_BID_YES` substring match includes `"what"`

**Severity:** medium (false-positive UX)
**Domain:** delivery
**Closed by:** [#41](https://github.com/protoLabsAI/ORBIS/pull/41). Dropped bare `"what"` from `_BID_YES` (multi-word `"what are they"` stays). Compiled both lists into word-boundary regexes (`\b...\b`) so "yesterday" no longer reads as "yes", "okayama" no longer as "okay", etc. 29 new tests cover the regression cases + the `_resolve_bid` integration paths.

---

## R7+R8. ✅ RESOLVED — non-atomic stash/drain

**Severity:** ~~low~~ medium (CR found a Major double-replay edge in the original fix)
**Domain:** delivery / persistence
**Closed by:** [#47](https://github.com/protoLabsAI/ORBIS/pull/47). Drain uses atomic-rename: `pending.json` → `pending.json.draining` → read → unlink. Stash uses fcntl-locked read-modify-write + `.tmp`-rename atomic write. Crash recovery: stale `.draining` files from prior crashed drains get absorbed on the next drain so items aren't orphaned. CR-flagged double-replay edge (when stale `.draining` was absorbed inside the lock AND its unlink failed AND no fresh pending arrived) closed via `absorbed_inside_lock` flag tracking. Test patches `Path.unlink` to fail to verify the regression. 13 tests.

---

## R9. ✅ RESOLVED — `_bid_issued` not persisted across reconnect

**Severity:** low
**Domain:** delivery
**Closed by:** [#43](https://github.com/protoLabsAI/ORBIS/pull/43) (v0.1.20). `snapshot_pending` stamps each pending item with the controller's current `bid_issued` flag. `replay_stashed` ORs the flags across replayed items: any `bid_issued=True` restores the controller flag before items go through `deliver()`, so the bid-then-drain gate sees the held state and waits for the user's next utterance instead of re-bidding. Replicated across items rather than stored separately because session_store is per-item-append. 7 tests.

---

## R10+R11. ✅ RESOLVED — WebRTC connect errors / no connecting state

**Severity:** medium (UX dead end + dead air)
**Domain:** frontend
**Closed by:** [#49](https://github.com/protoLabsAI/ORBIS/pull/49). New `web/src/plugins/status-pill/store.ts` exposes `pushStatusTransient(text, ms)` so callers outside the RTVI event surface can push UI feedback. `OrbStage.tsx` connect/disconnect catch handlers route errors through it (4s transient, matching RTVI Error duration). `StatusPill` now shows `"connecting…"` when transport is in `{connecting, authenticating, connected}`; the existing `BotReady` toast takes over once handshake completes. Externally-pushed transients win over RTVI-driven ones so connect errors don't get overwritten by stale toasts.

---

## R12. RTVI consumer is partial — bot-tts and bot-llm-stopped events are unwired

**Severity:** low (latent feature)
**Domain:** frontend

The server emits `BotTtsStarted` / `BotTtsStopped` / `BotLlmStopped` via `RTVIProcessor`, and `web/src/voice/hooks.ts:55-60` exposes them via `useBotTurnEvents`. No component consumes that hook (grep returns no callers).

The current `VoiceStateBridge.tsx` derives "bot is speaking" from `BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame` directly, which works for the orb shader pulse but doesn't surface the LLM-vs-TTS distinction (e.g. the orb can't show "LLM is responding but TTS hasn't started yet" — useful for slow-TTS warning).

**Fix sketch:** decide whether the granular events are wanted — if yes, wire them; if no, delete the hook so it doesn't drift.

---

## R13. ✅ RESOLVED — Self-modification tool round-trip is HTTP-only

**Severity:** ~~medium~~ **N/A — tools removed**
**Domain:** orb / frontend

Orb visual control (`set_variant`, `apply_palette`, `adjust_param`, `save_preset`, `recall_preset`) has been moved out of the LLM tool surface entirely. Orb state changes are handled by other processes outside the agent. This risk is no longer applicable.

---

## R14. ✅ RESOLVED — `text_agent` (A2A inbound) hard-codes env LLM

**Severity:** medium (config inconsistency)
**Domain:** llm / a2a
**Closed by:** [#44](https://github.com/protoLabsAI/ORBIS/pull/44). New `_resolve_skill_llm(skill) -> dict` helper owns the LLM routing precedence (persona override → env var → default, plus the `extra_body` kill-switch for custom URLs). Both `run_bot` and `text_agent` call it. `_text_clients` cache keyed on `(url, key)` keeps connection pooling while naturally segregating clients when overrides change. 15 tests cover the precedence ladder + extra_body kill-switch.

---

## R15. ✅ RESOLVED — Soft-neglect mood is set, not drifted

**Severity:** medium (became Major when audio-tags landed in #66)
**Domain:** personality / memory
**Closed by:** [#60](https://github.com/protoLabsAI/ORBIS/pull/60) + [#70](https://github.com/protoLabsAI/ORBIS/pull/70) + [#73](https://github.com/protoLabsAI/ORBIS/pull/73). Three-writer pattern on `PersonalityDAL`:

| API | Caller | Semantic |
|---|---|---|
| `set_mood` | Operator override (drawer / boot seed) | Snap-to-value |
| `drift_mood_toward(step=0.7)` | `apply_soft_neglect` (session-open) | Blend `step%` toward target |
| `drift_mood(*deltas)` | `AudioTagsTap` from #66 (per-turn) | Add delta; no-op when all-None |

Neglect now uses `drift_mood_toward(step=0.7)` so the per-turn drift from audio-tags isn't blown away on session-open. With `step=0.7`, the original "visible from turn one" requirement still holds: a 7-day gap from a `+0.6` valence baseline lands at `+0.005` (visibly negative-ward) instead of `-0.35` (the raw target). 13+ tests including a compose-with-per-turn-writes regression.

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
