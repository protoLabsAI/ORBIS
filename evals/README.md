# Agent evals

A repeatable harness for the **main agent's decision turn** — so refining the
orchestration prompt is *measured*, not vibes. Each scenario sends one user
utterance through the same prompt blocks and tool schemas the live voice agent
uses (persona + tool-use + capabilities + **fleet** + repair + audio, and the
full voice tool surface incl. `delegate_to` and `orchestrate`), against the
configured LLM with a **fixture fleet**, then scores the result.

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

## Known gap

This harness measures the **decision** (what the agent chooses to do). It does
**not** measure **presence/latency** — e.g. the "where'd you go" dead-air when a
single slow `delegate_to` runs without a reassurance line. That's a runtime
pipeline timing property, not a decision-turn property; it needs a separate
live-pipeline harness. Tracked as future work.

## Baseline

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
