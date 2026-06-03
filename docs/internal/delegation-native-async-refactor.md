# Delegation refactor → Pipecat-native async function calls

Status: **PLAN** (2026-06-02). Greenfield — no back-compat with the old flows.
Owner: kj. Supersedes the custom DeliveryController-based delegation backgrounding.

## Why

ORBIS reinvented Pipecat's async function-call mechanism on top of a **sync**
registration, and the two fight:

- `delegate_to` is registered `cancel_on_interruption=True` (**sync**), but my
  non-blocking change made the handler background manually (`asyncio.create_task`
  + `DeliveryController`) and return an immediate ack string. So Pipecat runs the
  LLM on that ack (**ack #1**) *and* the `on_function_calls_started` opening
  filler fires (**ack #2**) → they race → "answered, then a microack."
- Progress + final answers go through `DeliveryController` (with the announcer's
  "reply from ava" framing) instead of Pipecat's native result injection — the
  source of the framing/ordering bugs.

Pipecat already has the right primitive (verified in 1.0.0 source + docs):

> **`cancel_on_interruption=False`** → the call is **async**: *"the LLM continues
> the conversation immediately without waiting for the result, and the result is
> injected later via a developer message."*
> Intermediate `result_callback(..., FunctionCallResultProperties(is_final=False))`
> updates are *"injected into the LLM context as async-tool developer messages and
> do not close the function call until the final result is sent."*

That is exactly a delegation: continue (ack) → progress → answer.

## Scope decision

**Native function-call flow**, NOT the `pipecat-flows` state-machine framework
(not installed; wrong fit for a free-form voice agent). Greenfield: replace, don't
preserve, the old delegate flows.

## Target design

- **One `delegate_to` tool**, registered **`cancel_on_interruption=False`**
  (async-native). The LLM's immediate continuation *is* the ack — one ack,
  natural, in-context. No ack-string return, no `asyncio.create_task`.
- Handler `await`s the dispatch (Pipecat backgrounds it) and emits:
  - `result_callback(text, is_final=False)` for each **real** progress update
    (A2A `status.message` / tool-call frames — already captured by the streaming
    work in a2a_outbound),
  - `result_callback(answer)` final → injected as a developer message → the LLM
    narrates it in its own voice (no announcer "reply from X" framing).
- **Drop `delegate_async`** — native-async `delegate_to` is already non-blocking,
  so the separate fire-and-forget tool is redundant. One delegation tool; the LLM
  no longer has to pick.
- **`orchestrate`** → native async too: per-step `is_final=False` progress + final
  synthesis result; drop its DeliveryController path.
- **`on_function_calls_started` opening filler** → removed for tool turns (the LLM
  continues natively). Keep the result→LLM path for any genuinely sync tool.
- **`DeliveryController`** → retained ONLY for true out-of-band proactive delivery
  (reminders, push) — NOT function-call results. Remove `_spawn_delegate_delivery`,
  the delegate nudge, and the speak_now/deliver-for-delegate paths.

## Phases (each its own commit; systematic)

1. **Branch + housekeeping.** Refactor branch off the streaming work (reuses
   a2a_outbound's real-status-text capture). Park the wake-word foundation files
   on their own branch so the tree is clean.
2. **Native `delegate_to`.** New handler (await + `result_callback` is_final
   progress + final + error). Register `cancel_on_interruption=False`. Remove the
   `_wrap_sync`/immediate-ack/`_spawn_delegate_delivery` path. Drop `delegate_async`.
3. **Progress plumbing.** Thread a `result_callback`-backed progress sink from the
   handler → `delegate_dispatch` → a2a_outbound's `progress_callback`
   (`is_final=False`). a2a_outbound already yields the agent's real status text.
4. **Opening-filler reconciliation.** Skip the `on_function_calls_started` filler
   for async tool turns; reconcile with the micro-ack/filler system.
5. **`orchestrate` → native async.** Per-step `result_callback(is_final=False)`;
   final synthesis via `result_callback(...)`. Drop its DeliveryController usage.
6. **DeliveryController scope-down.** Remove delegate-result paths; keep
   reminders/proactive. Delete now-dead helpers.
7. **Tests.** Rewrite delegate/orchestrate tests for the native pattern: capture
   `properties` (is_final/run_llm), assert ONE ack, progress injected as
   intermediate, final narrated, error path. Update `FakeParams.result_callback`.
8. **Live verify.** Rebuild + voice: one natural ack; real progress when Ava emits
   it; answer narrated in-context; no double-ack / out-of-order. Walk the HANDOFF
   agents-&-delegation checklist.
9. **Cleanup + docs.** Remove dead code; update STATUS/DECISIONS; fold/close #380
   (its a2a_outbound streaming is reused; the speak_now beat is replaced).

## Risks / decisions to watch

- **Politeness:** native final-result injection runs the LLM as soon as the answer
  lands. `DeliveryController` gated to `next_silence` (don't talk over the user).
  Verify Pipecat's interruption handling covers this; add gating if it talks over.
- **Ack quality:** the LLM's "continue immediately" ack is prompt-dependent. May
  need a system-prompt nudge ("when you hand off, briefly acknowledge, then stop").
- **Instant-lead feel:** removing the opening filler trades the instant canned
  "okay, on it" for the LLM's continuation (slight latency). Verify it still leads
  the wait acceptably; if not, a minimal sync filler for tool-start only.

## Verification

- Unit: native `delegate_to` (is_final progress, final, error, single result→LLM);
  `orchestrate`; a no-double-ack harness.
- Live: the three ack scenarios + the HANDOFF "Agents & delegation" checklist.
