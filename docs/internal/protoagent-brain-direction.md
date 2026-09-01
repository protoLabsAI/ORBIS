# protoAgent as the brain — direction + execution plan

**Status: ACTIVE (locked 2026-08-17). Supersedes the orchestration-layer
ambitions of `duplex-orchestration-direction.md`; qualifies (does not
touch) `native-audio-direction.md`.**

ORBIS keeps the voice layer as planned — native audio, orb, Pipecat
pipeline, and the voice-specific loop (fillers/acks, presence,
barge-in, personas, prosody). Everything underneath — the tool loop,
multi-step orchestration, long-term memory, background/long-running
work, trajectory, extensibility — is **delegated to a protoAgent hub**
instead of being grown in-process. The hub orchestrates the fleet
(the fleet agents are protoAgent forks, so hub→fleet delegation is
native to that runtime). End state: a JARVIS-like voice interface that
controls the whole system and fleet through one brain.

This *activates* a decision already on the books: DECISIONS.md
"Explicitly out of scope" rejected bundling/vendoring protoAgent **in
favor of pure delegation**. This doc is that delegation, promoted to
primary.

---

## Why now (evidence from the 2026-08-17 cross-repo audit)

Two deep audits were run: one mapping protoAgent's agentic machinery
(v0.138.0), one auditing ORBIS's loop. They converge:

**ORBIS's weakest points are things protoAgent already solved.**

| ORBIS gap (audited) | protoAgent answer |
|---|---|
| Delegated work has no task identity — an in-flight `delegate_to` is an anonymous coroutine; barge-in **silently drops the result** (`agent/tools.py` barge_epoch gate), disconnect cancels it, restart loses it | Durable background-job registry, exactly-once `notified` flag, push-resume into the origin session, batch coalescing (ADRs 0050/0070) |
| `orchestrate()` has no plan/state object, can't be monitored/cancelled/resumed, **never live-tested** | Goal · tasks · schedule · watch as one OODA loop, durable stores, "yield and come back" doctrine |
| No trajectory — `sessions.tool_calls` column exists and is always empty | Refs-only JSONL trajectory log (ADR 0102) |
| Memory's interesting half is unwired — `facts.add()` and both FTS5 `search()` paths have **zero production callers**; recall = last-3 `final_output` + `summary.txt`; #625 summary-poisoning open | Knowledge store with trust tiers, untrusted-reference envelope, per-turn injection audit (ADR 0069) |
| Zero spans/metrics on the delegation path (`orchestrate.py`, `delegates.py`, adapters, `acp/client.py`) | Per-turn telemetry incl. cache tokens, tool durations, context fill; joinable W3C trace ids over A2A |
| HITL is a single global `PendingAsk` slot — concurrent asks clobber | `input_required` is a first-class A2A producer event, keyed per task |
| No Python-side extension point (tools = editing 1285-line `agent/tools.py`) | 21-method plugin registry — capabilities get added to the **brain**, not to ORBIS |

**Two prior blockers are gone:**

1. protoAgent's `<scratch_pad>/<output>` text protocol was deleted
   (#1411/#1412); it streams natively and its A2A producer contract
   emits `delta` / `tool_start` / `tool_end` / `input_required` /
   `done` events. Hub latency is measurable, not structural.
2. ORBIS deferred A2A push hardening because "a cloud delegate can't
   reach 127.0.0.1". A **local** hub can. Push-back + durable outbound
   task handles are buildable now.

**The seam mostly exists.** ORBIS's A2A delegate adapter (streaming +
health probes) and mDNS discovery (`_protoagent._tcp`) already interop;
the live user config already delegates to two protoAgent instances.

## Division of labor

- **ORBIS owns:** native audio → AVAudioEngine (per
  `native-audio-direction.md`, unchanged), orb viz, Pipecat pipeline,
  fillers/acks, presence + delivery policy, barge-in, personas,
  widgets, voice tools, reminders/inbox, the `tool_loop.py` brake.
- **protoAgent hub owns:** tool execution at depth, multi-step
  orchestration, subagents, background/long-running work, long-term
  memory/knowledge, trajectory, plugins, evals, fleet delegation.
- **Ops accepted:** the hub runs as a persistent local service
  (launchd or Docker) beside ORBIS.app. On iOS (Phase 4 of the
  native-audio plan) the hub is reached over the tailnet.

---

## Execution phases

Ordered by churn: bank low-churn high-value first; high-churn items
get their own live-tested PR.

### Phase A — wire the hub

- [ ] Run a protoAgent instance locally. The bundled delegate now targets the
      production endpoint at `:7870`; launchd/service installation remains an
      operator responsibility.
- [x] Bundle a `hub` A2A delegate row, with a description written so the
      router prefers it for multi-step/fleet/background asks. Untouched
      persistent `:7871` seeds migrate atomically; custom hubs are preserved.
- [ ] Validate dispatch through ORBIS's real adapter path (registry →
      `A2ADelegateAdapter.dispatch`) and measure time-to-first-delta
      on a trivial and a tool-using prompt.
- [ ] Spoken smoke test in the next live QA pass.

### Phase B — durable outbound task handles (the big win)

Kills audited gap #1: work no longer dies with the turn.

- [ ] `outbound_tasks` table (task id, delegate, origin session, goal
      preview, status, created/updated) in `memory/db.py` — written on
      every A2A dispatch that returns a task id.
- [ ] Register `pushNotificationConfig` with the hub on dispatch
      (localhost callback → existing `/a2a/callback`).
- [ ] `/a2a/callback` correlates by task id → marks the row terminal →
      routes the result through `DeliveryController` (speak at next
      silence) or stashes it if no session is live.
- [ ] On barge-in: **keep the handle** (drop only the in-turn
      narration). On reconnect/restart: requery non-terminal tasks via
      A2A `tasks/get` and deliver or resume.
- [ ] Presence policy upgrade: for hub delegations, acknowledge →
      yield the turn → one coalesced briefing on completion (adopt
      protoAgent's push-only doctrine; no progress narration loop).

### Phase C — HITL + structured progress

- [ ] Map hub `input_required` events onto AskGate, keyed by task id —
      replaces the single-process-wide `PendingAsk` slot
      (`agent/user_state.py`).
- [ ] Consume `tool_start`/`tool_end`/`delta` stream events →
      structured `delegate.*` SSE → StatusPill / logs panel (the
      "present but unused for UI" wiring noted in `widgets.md`).
- [ ] Verbal cancel: "stop that" cancels the hub task via A2A
      `tasks/cancel` (this is the long-open layer-2 cancel, now
      buildable because tasks have identity).

### Phase D — the shed

- [ ] Retire `agent/orchestrate.py`: `orchestrate(goal)` becomes a
      thin alias for delegating the goal to the hub (or is dropped and
      the router just picks the hub). Delete the never-live-tested
      loop, its single-slot HITL plumbing, and its tests.
- [ ] Stop growing `memory/facts.py` + FTS retrieval — long-term
      memory reads/writes go to the hub's knowledge store. Keep
      session recall (last-3 + summary) for conversational continuity.
- [ ] Fold the #625 memory-hygiene fix into this: session summaries
      stop being durable memory, so the poisoning vector shrinks to
      one session's context.

### Phase E — cheap steals from the protoAgent audit (each its own small PR, bankable anytime)

- [ ] **Reasoning-stripper at the speak boundary** — strip
      balanced/orphan `<think>`/`<scratch_pad>` blocks before TTS
      (belt over #645's `enable_thinking:false`); a leaked reasoning
      block read aloud is the worst-case voice UX failure. Backtick-
      guarded regexes per protoAgent `graph/output_format.py`.
- [ ] **Persist `tool_calls`** into the existing empty
      `sessions.tool_calls` column — minimum-viable trajectory.
- [ ] **Delegation-path observability** — Langfuse spans in
      `delegate_adapters.dispatch` / orchestrate steps; counters for
      dispatch success/failure/latency, barge-drops, ask timeouts,
      presence fires, tool-loop guard fires.
- [ ] **Trace propagation on the A2A adapter** — send
      Langfuse/W3C headers on dispatch (only the OpenAI adapter does
      today); protoAgent validates and **joins** caller trace ids, so
      fleet-wide traces stitch end to end.

## Explicitly not doing

- Porting protoAgent's plugin system, LangGraph middleware stack, or
  knowledge store **into** ORBIS — the hub owns them (audit verdict:
  a plugin system solves a contributor-scaling problem ORBIS doesn't
  have).
- Putting the hub in the conversational hot path (`/v1` as the voice
  LLM). Router-first stands: ORBIS's fast model answers; the hub gets
  the substantial work. Revisit only if Phase A latency measurements
  are surprisingly good.
- Resurrecting any browser/web surface for voice — the hub's console
  is an operator tool, not the voice interface.

## Open questions

- Hub ops shape for daily use: launchd plist vs Docker; who owns
  starting it (ORBIS boot gate could probe + warn).
- Whether `jobcoach`/`frank`-style direct delegates remain
  first-class or become hub-routed (lean: leave them during
  transition; the hub's own delegate registry can absorb them later).
- Hub-side voice affordance: whether briefs need a "speakable"
  length/style hint on the A2A request (likely a metadata field, not
  a fork).
