# Agent-to-agent (A2A)

When ORBIS hands work to one of your [delegates](/reference/agent#delegates), it
might be talking to *another agent* — not just calling a model. The way it does
that is **A2A**, the Agent2Agent protocol. This page explains what A2A is and
why it lets ORBIS plug into a whole fleet of agents.

## What A2A is

A2A is an open, standard way for AI agents to **talk to each other** — a
JSON-RPC contract over HTTP for sending a message to an agent, holding a
conversation across turns, and streaming progress back. It's the "USB-C for
agents": any agent that speaks A2A can work with any client that speaks A2A,
with no bespoke integration.

ORBIS is an A2A **client**. Add an A2A delegate, and the orb knows how to send it
a request and interpret what it sends back.

## Why it matters

Because A2A is a standard, **any A2A agent becomes a delegate** — drop in its
URL and a description, and the orb can route work to it. ORBIS stays a fast
conductor; the specialists in your fleet do the deep work. No glue code per
agent.

## What you get over a plain model call

An [OpenAI-compatible delegate](/reference/agent#delegates) is a single,
stateless model call. An A2A delegate is a **full agent interaction**:

- **It remembers the thread.** ORBIS keeps a conversation handle with each A2A
  delegate, so you can follow up — "and dig into that incident" — and the
  delegate has the context.
- **It streams progress.** A long task can report as it goes; the orb narrates
  that progress so you're not sitting in silence.
- **It has a lifecycle.** A delegate can finish, fail, or come back asking for
  **more input** — ORBIS handles each: it answers from context if it can, or
  surfaces the question to you.
- **It describes itself.** Agents advertise their capabilities (streaming,
  multi-turn) via an "agent card"; ORBIS adapts to what each one supports.

## How a hand-off flows

1. You ask for something that fits a delegate's description.
2. The orb acknowledges immediately and sends the request over A2A.
3. The delegate works — streaming progress if it can.
4. The orb speaks the synthesized result, attributed to the delegate.

For multi-step asks, ORBIS can **orchestrate** several A2A delegates in the
background — see [The agent model](/explanation/the-agent-model#sync-vs-background).

## See also

- [Agent reference → Delegates](/reference/agent#delegates)
- [The agent model](/explanation/the-agent-model)
- [Add a delegate](/how-to/add-a-delegate)
