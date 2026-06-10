# Agent evals

Repeatable harnesses for refining the main agent *measured, not vibes*. Two so
far, measuring two different things:

- **`run.py` — the decision turn** (routing + grounding). Sends one utterance
  through the real prompt blocks + tool schemas against the live LLM. See below.
- **`presence.py` — dead-air / "where'd you go"** (does the user keep hearing a
  sign of life while a slow tool runs). Deterministic, no LLM. See
  [Presence / dead-air](#presence--dead-air).

---

## Decision turn (`run.py`)

A repeatable harness for the **main agent's decision turn** — so refining the
orchestration prompt is *measured*, not vibes. Each scenario sends one user
utterance through the same prompt blocks and tool schemas the live voice agent
uses (persona + tool-use + capabilities + **fleet** + grounding + repair + audio,
and the full voice tool surface incl. `delegate_to` and `orchestrate`), against
the configured LLM with a **fixture fleet**, then scores the result.

This is a **manual, offline harness — not pytest**. It hits a live LLM, is slow,
and is nondeterministic, so it doesn't belong in CI. Run it by hand when you
touch the prompt, the tool schemas, or the orchestration loop.

## Run

```bash
.venv/bin/python evals/run.py                # all scenarios
.venv/bin/python evals/run.py -s grounding   # filter by id substring
.venv/bin/python evals/run.py --no-judge     # routing only (skip the LLM judge)
```

Output is a per-scenario table (`route` / `ground` / `overall`) plus a full
JSON dump at `evals/last_report.json` (gitignored).

### Which LLM

By default it reads the `llm` block in `config/orbis.yaml` and uses
`router_model` if a two-model (orbis-3it) split is configured, else `model` —
i.e. whatever actually makes the routing decision in production. Override with:

```bash
EVAL_LLM_URL=... EVAL_LLM_MODEL=... EVAL_LLM_KEY=... .venv/bin/python evals/run.py
```

## What it scores

- **routing** (deterministic) — did it call the expected tool / hand off to the
  expected delegate? `none` = a spoken answer with no tool; `any_handoff` =
  `delegate_to` *or* `orchestrate`; a tool name = exactly that tool.
- **grounding** (LLM-as-judge) — for `must_not_fabricate` scenarios, did it
  delegate to a relevant agent, hedge, or admit it can't — rather than invent a
  fact, a number, or an agent/capability that isn't on the fleet? The judge is
  given the fixture fleet so "delegated to a real, relevant agent" counts as
  grounded.
- **brevity** — for tool-less spoken answers with a `max_chars` budget.

A scenario only scores the dimensions its `expect` declares; unchecked
dimensions show `—`.

## Add a scenario

Append to `scenarios.yaml` — that's the whole job (no code change for the common
cases):

```yaml
  - id: my_case
    fleet: lean          # optional: omit (→ default_fleet), name a fleet, or inline a list
    utterance: "what the user says"
    expect: { tool: delegate_to, target: ava, grounding: must_not_fabricate, max_chars: 240 }
```

`fleet:` resolves a string against the top-level `fleets:` map (reusable
rosters), an inline list, or — if omitted — `default_fleet`. The hardest
grounding cases use the `lean` fleet (no research agent) to remove the
easy delegate-to-researcher escape hatch, forcing an honest hedge/admit.

### Scope

`run.py` measures the **decision** (what the agent chooses to do). It does not
measure **presence** — whether the user keeps hearing a sign of life while a slow
tool runs. That's what `presence.py` is for (below).

### Baseline (decision turn)

On `protolabs/fast` (2026-06-08): **15/15**.

The first harness baseline was 14/15 — `grounding_invented_delegate` was a real,
stable bug: a request for a nonexistent "design agent" got misrouted to the
(unrelated) research agent instead of an honest "I don't have a design agent."
The `grounding_block` honesty guardrail (`agent/filler.py`, wired into
`_effective_prompt` right after the fleet block) closed it — the agent now says
"I can't do that, I don't have a design agent in my fleet" — with no regression
on the other 14. That's the harness working as intended: it caught the
fabrication, the fix is measured against it, and it stays a regression guard for
future prompt changes.

---

## Presence / dead-air

`presence.py` measures the **"where'd you go?"** failure: a slow tool runs, the
LLM is blocked on the result and can't narrate, and the user is left in silence.
It's **deterministic — no LLM, no pipeline** — so it can run anywhere and back a
pytest regression guard.

For each profile (a tool, how long it takes, and when the delegate streamed
`note_progress`) it derives the user-audible timeline from the **real policy**
(`agent/presence.py`, driven by the real `latency_for` / `ASYNC_TOOL_NAMES` /
filler `Settings`) and reports the largest **dead-air gap** against a presence
SLA (default 8 s, anchored to the stall-watchdog's threshold).

```bash
python evals/presence.py             # all profiles, 8s floor
python evals/presence.py --floor 6   # stricter
python evals/presence.py -s delegate # filter by id substring
```

`agent/presence.py` is the single source of the presence schedule; `app.py`'s
`on_function_calls_started` should call it so the two never drift (the fix that
closes the gap below is a change to that one function).

### Baseline (presence) — **3/8 within the 8 s floor**

The harness reproduces and *quantifies* the live "where'd you go":

| profile | gap | why |
|---|---:|---|
| `delegate_no_stream` | **24.4 s** | slow async delegate, never streams progress → opening ack, then silence to the answer |
| `delegate_one_early` | 27.0 s | one early check-in, then a long void |
| `delegate_sparse` | 32.0 s | long delegate, a single sparse update |
| `sync_slow_long` | 18.0 s | even a SLOW **sync** tool goes silent after the two-line loop (6 s, 12 s) |
| `medium_runs_long` | 14.4 s | a `medium`-classified tool that runs long gets the ack, then nothing |

Root cause: the opening ack covers t≈0, the `_progress_loop` covers SLOW **sync**
tools at 6 s/12 s (then stops), and **async** tools (`delegate_to`, `orchestrate`)
get *no time-based loop at all* — they depend entirely on the delegate streaming
`note_progress`. The stall-watchdog can't help: it stands down the instant the
tool starts. The fix (next): give async delegates a time-based presence fallback
that defers to real streamed progress when it's flowing — re-measured here.

(Bonus finding: `orchestrate` derives as `medium`, not `slow` — a latency
mis-classification worth fixing alongside.)
