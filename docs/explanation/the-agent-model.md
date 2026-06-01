# The agent model

ORBIS's brain is deliberately a **small, fast model that routes** — not a giant
model that does everything. This page explains that choice and how delegation,
orchestration, and tools fit around it.

For the knobs, see the [Agent reference](/reference/agent).

## Router first

Every turn, the LLM makes a routing decision before anything else:

- **Answer directly** — most conversation.
- **Call a tool** — a concrete action (set a reminder, list reminders).
- **Delegate** — hand a heavier task to a specialist agent.

Keeping the routing model small and fast is what makes the orb feel responsive.
The heavy thinking, when it's needed, happens *elsewhere* — in a delegate — and
the orb narrates the result. (If you want a stronger model for just the
decision turn, that's what [two-model routing](/reference/agent#advanced-llm-options)
is for.)

## Delegates: specialists the orb calls

A **delegate** is another agent ORBIS can hand work to. You give each one a
**name** and a **description**; the LLM reads the descriptions and picks the
right delegate for the task. Two kinds:

- **A2A agents** speak the **Agent2Agent** protocol — a standard JSON-RPC
  contract for agents to message each other, keep a conversation across turns,
  and stream progress. This is how ORBIS talks to other agents in a fleet.
- **OpenAI-compatible** delegates are any `/v1/chat/completions` endpoint
  treated as a sub-agent (a larger model, a specialized service).

The point: the orb stays a fast conductor; the delegates do the deep work.

## Sync vs. background

Delegation comes in two shapes:

- **Delegate (sync-ish).** The orb hands off one task, waits, and speaks the
  answer. It acknowledges immediately ("on it…") so it isn't silent, and it can
  narrate progress a delegate streams back.
- **Orchestrate (background).** For a multi-step ask ("get the fleet status,
  then dig into any incident"), the orb runs a **bounded loop** in the
  background — calling delegates step by step, keeping context with each one —
  and reports the synthesized result when it's done. You can keep talking
  meanwhile.

Both are designed so a slow or unreachable delegate can't hang the orb: every
step is time-bounded, and a dropped connection falls back gracefully.

## Reminders and other actions

Actions the orb can take itself (not delegated) are **tools** — small, exact
capabilities. Reminders are the headline example: set, list, and cancel them by
voice, and see them in the [reminders bell](/reference/agent#what-the-agent-can-do).
A tool result is fed back to the model so it knows the action succeeded and can
confirm it to you.

## Why this shape

- **Responsiveness** — a small router replies fast; depth is opt-in.
- **Composability** — any A2A or OpenAI-compatible agent becomes a delegate
  without changing ORBIS.
- **Resilience** — time-bounds and failover keep the orb talking even when a
  model or delegate misbehaves.

## See also

- [Agent reference](/reference/agent)
- [Add a delegate](/how-to/add-a-delegate)
- [How ORBIS works](/explanation/how-orbis-works)
