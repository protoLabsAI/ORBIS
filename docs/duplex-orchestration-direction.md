# Duplex orchestration — router-first tool execution (D1)

Status: **design** · drafted 2026-05-30 · owner: kj · issue: `orbis-syq` (D1),
depends `orbis-1c4` (E1 barge-in=abort)

This is the design behind the reframed D1. It exists because of a concrete,
reproducible bug and a strategic direction Josh set ("go to our orchestration
and delegation pipeline… we want a duplex system"). Read this before touching
the voice LLM cycle, `agent/filler.py:tool_use_block`, or `voice/llm/`.

---

## The bug that forced this

Symptom (live, 2026-05-30): ask the orb to set a reminder / hand off a task →
it **speaks** ("yeah, let me set that for you…") and then **nothing happens**.
No reminder, no hand-off. It announced a tool call it never made.

### Root cause (confirmed, not theorized)

The default gateway voice path builds pipecat's `OpenAILLMService` (or the
`TwoModelOpenAILLMService` subclass) — see `voice/llm/__init__.py:154-190`.
Both consume the provider's **native structured `tool_calls`** over the
streaming adapter. The custom `<tool_call>`-tag stream parser
(`voice/llm/_qwen_tool_parser.py`) is **only** wired for the local MLX path,
not the gateway. So tool-call *parsing* is not the failure.

The failure is **model behavior, induced by our own prompt**. The `TOOL USE`
block (`agent/filler.py:240`, `tool_use_block()`) instructs:

> "speak BEFORE every tool call — emit one short preamble line in the response
> FIRST, then call the tool."

We are asking **one** streamed completion from the **fast** model to do two
things in order: (1) emit conversational preamble tokens, then (2) emit a
`tool_calls` block. Weak/fast models reliably do (1) and then end the turn
(`finish_reason=stop`, no `tool_calls`) — the spoken sentence reads, to the
model, as a complete and satisfying answer. The `capabilities_block`
("call the tool, don't just say it", `agent/tools.py:151`) is a prompt-level
band-aid fighting the *very next* block that tells the model to talk first.

The reliable counter-example already lives in the repo: the **A2A text path**
(`app.py:853`) runs a clean non-streaming ReAct loop — `tool_choice="auto"`,
native `msg.tool_calls`, bounded iteration — and it does **not** prepend a
spoken preamble. It just works. The voice path is unreliable *because* of the
preamble-first coupling, not despite it.

---

## What the field does (research, 2025–2026)

- **STT → Router → TTS.** The mature cascaded pattern puts a *router/decision*
  step between understanding and generation. "Decoupling understanding
  (AudioLLM) from generation (TTS) allows independent optimization." The tool
  decision is its own step; narration is its own step.
- **Full-duplex is the frontier.** Systems that "listen and speak
  simultaneously, handle interruptions gracefully, and make real-time
  turn-taking decisions" (Moshi, FireRedChat, τ-Voice benchmark). The relevant
  takeaway for us is not an end-to-end speech model — it's the *decoupling*:
  the decision/▶action loop must run without blocking the listen/▶speak path.
- **Two-model routing** (a stronger model decides, a faster model narrates) is
  a recognized cost/latency pattern — which ORBIS already has scaffolded in
  `voice/llm/two_model.py` but currently only switches *model per call*; it
  does not change the preamble coupling that breaks tool emission.

## What Pipecat gives us for free (verified against docs)

- **`FunctionCallResultProperties(run_llm=False)`** + `on_context_updated`
  callback — decide whether the LLM runs *after* a tool, and trigger narration
  manually. Lets us chain tool calls and control exactly when narration fires.
- **Async function calls** — register with `cancel_on_interruption=False`:
  "the LLM continues the conversation immediately without waiting for the
  result, and the result is injected later via a developer message." This is
  Pipecat's *native* delegate-async primitive. `is_final=False` marks
  intermediate results → progress narration.
- **`ParallelPipeline` + `FunctionFilter`** — fan the same upstream frames to
  multiple branches (e.g. a decision branch and a listen/ack branch) and merge
  downstream. **Producer/Consumer** processors move frames between branches.
- **External turn management (`UserTurnProcessor`)** — centralized turn
  detection that survives a branched pipeline, for real duplex turn-taking.

---

## The ORBIS design — router-first, decoupled, duplex-ready

Three things that today ride on one fragile streamed completion get split:

| Concern | Today | Target |
| --- | --- | --- |
| **Acknowledge** ("I heard you") | preamble tokens from the fast model, in the same turn as the tool | micro-ack / filler layer (`agent/filler.py`, micro model) — fires on user-stop **regardless of whether a tool is called** |
| **Decide + execute** the tool | same streamed turn, after the preamble → dropped | a decision turn with **no preamble obligation**, native `tool_calls`, on the **router** model; long/▶heavy tools go async (`cancel_on_interruption=False`) and are orchestrated |
| **Narrate** the result | same turn / post-tool | content/▶micro model, post-tool, via `run_llm` + DeliveryController |

Because acknowledgement is decoupled, the user always hears "on it" even on the
rare empty decision — and the tool decision, freed from "talk first," emits
reliably.

### D1 proper — the orchestration loop

D1 is an **async orchestration tool** (`orchestrate` / `research`) that, once
the decision turn calls it:

1. Acks immediately (DeliveryController, like `delegate_async`).
2. Runs a **bounded** loop reusing the A2A ReAct engine (`app.py:853`) —
   `tool_choice=auto`, native `tool_calls`, capped iterations — chaining
   `delegate_to` / tool calls toward the goal. **Heavy reasoning stays in the
   delegates** (DECISIONS.md:59); ORBIS orchestrates, it does not reason
   in-process.
3. Narrates progress via DeliveryController (`is_final=False` intermediate
   results), delivers the synthesized result when done.
4. D2 (`orbis-2lx`) adds the goal/stall/fail-safe-done guards on this loop.

### Duplex posture

We are **not** adopting an end-to-end speech model. "Duplex" here = the
decision/orchestration loop never blocks listen+speak: barge-in aborts it (E1,
`orbis-1c4`), the stall watchdog covers dead air (E2, done), and async tools +
DeliveryController carry results back at a natural pause. `ParallelPipeline`
is the escape hatch if we later need the ack/listen branch to run truly
concurrently with a decision branch.

---

## Build plan (phased, each its own PR)

1. **Decouple the preamble (the bug fix).** Stop `tool_use_block` from forcing
   "speak before the tool." The decision turn makes the call; the existing
   micro-ack/filler covers acknowledgement. This alone fixes the reported bug.
   *Smallest change that restores correctness — but per Josh, ship as the first
   step of the real architecture, not as a standalone band-aid.*
2. **Router-decision turn.** Make the decision turn explicitly preamble-free on
   the router model; keep narration on content/micro. Extends
   `two_model.py` from "switch model" to "switch model **and** turn shape."
3. **D1 orchestration tool.** `orchestrate`/`research` async tool over the
   bounded ReAct engine, DeliveryController ack/progress/result.
4. **D2 guards.** Goal/stall/fail-safe-done on the loop (`orbis-2lx`).

## Open questions

- Force `tool_choice` (vs `auto`) on the decision turn when intent is clearly
  actionable, or keep `auto` and rely on the stronger router model? Lean
  `auto` first; measure.
- Does the decision turn stay inside pipecat's LLM service (subclass override)
  or move to a custom `FrameProcessor` that calls the gateway directly (like
  the A2A loop)? Subclass first; promote to a processor only if the cycle
  fights us.
- Where the micro-ack fires for *tool* turns vs plain turns — needs to not
  double-speak with narration.
