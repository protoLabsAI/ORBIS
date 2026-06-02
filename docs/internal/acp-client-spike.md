# Spike: ORBIS as an ACP client (voice-drive your coding agents)

**Status:** spike / design only — no code. **Date:** 2026-06-02.
**Idea (Josh):** let people use ORBIS to drive their **protoCLI, Claude Code,
Codex, or OpenCode** systems. "We have the pattern in several places to adapt —
so then it's also an ACP client."

## TL;DR

ACP (Agent Client Protocol) is JSON-RPC 2.0 over a subprocess's stdio, with a
clean client/agent split. ORBIS already has every moving part:

- **JSON-RPC + streaming + a persistent context id** → `a2a/client.py`
  (`dispatch_message_stream`, `contextId`, `progress_callback`).
- **A typed, pluggable delegate registry** → `agent/delegates.py`
  (`Delegate.type ∈ {"a2a","openai"}`, `_parse_entry` switches on type).
- **A drive-to-completion voice tool** → `orchestrate(goal)` (orbis-20l / the
  A2AClient work) and `delegate_to`.
- **Launch + supervise a child process over a pipe** → the fleet launcher in
  `src-tauri/src/lib.rs` (free-port spawn, stdout tee, reap-on-quit) and the
  sidecar supervisor (`supervise_sidecar`).

So "ORBIS is also an ACP client" is mostly **a third delegate transport**:
`type: "acp"` whose `dispatch` launches the agent binary and speaks ACP over
stdio instead of A2A/JSON-RPC over HTTP. The genuinely new work is the
**client-side service surface** ACP requires (permission prompts, file
reads/writes, terminal) — and rendering those over **voice** is the
interesting design problem, not the transport.

## What ACP is (for a client implementer)

- **Transport:** JSON-RPC 2.0. The agent "typically runs as a subprocess," so in
  practice the client launches it and frames JSON-RPC over **stdin/stdout**
  (newline-delimited). One process per agent.
- **Handshake (client → agent):** `initialize` (negotiate version + exchange
  capabilities), `authenticate` (only if the agent demands it).
- **Session methods (client → agent):** `session/new`, `session/prompt` (send a
  user turn), `session/load` (resume — optional capability), `session/cancel`
  (notification), `session/set_mode` (optional).
- **Streamed back (agent → client):** `session/update` notifications carrying
  agent/user/**thought** message chunks, **tool calls + tool-call updates**,
  **plans**, available-commands, mode changes.
- **Client must IMPLEMENT (agent → client):** `session/request_permission`
  (authorize a tool call), and — optional, gated by the capabilities the client
  advertises — `fs/read_text_file`, `fs/write_text_file`, and the `terminal/*`
  family (`create`, `output`, `wait_for_exit`, `kill`, `release`).
- **Capabilities:** negotiated in `initialize`. The client declares what it can
  serve (fs? terminal?); the agent declares what it supports (load? modes?).
  Optional features require explicit advertisement on both sides.

Source: <https://agentclientprotocol.com/protocol/overview>.

## Which agents speak it (today)

All four the user named are reachable, plus more — so this isn't bespoke per
agent:

- **Claude Code** — via Zed's SDK adapter (`claude-code-acp`).
- **Codex CLI** — via Zed's adapter.
- **Gemini CLI** — native ACP.
- **OpenCode** — native ACP (`opencode.ai/docs/acp`).
- **protoCLI** — our own; we'd add an ACP **server** adapter to it (the mirror
  of this client). That makes protoCLI drivable from Zed/VS Code too — a nice
  side win.

Sources: [Zed external agents](https://zed.dev/docs/ai/external-agents),
[OpenCode ACP](https://opencode.ai/docs/acp/),
[ACP agents list](https://agentclientprotocol.com/get-started/agents),
[vscode-acp (Claude/Codex/Gemini/OpenCode/Qwen/…)](https://github.com/formulahendry/vscode-acp).

## Why ORBIS is a natural ACP client

ACP assumes the **client owns the workspace** — it's the editor that reads/writes
files and runs terminals on the user's behalf, and the agent asks permission.
ORBIS is **single-owner, native, Apple-Silicon, runs on your hardware** — the
files and shell the agent wants are *right there on the same machine*. That's
exactly the client role, minus a text editor. Voice becomes the surface: "ask
Claude Code to add tests to the payments module," then ORBIS narrates the plan,
the tool calls, and surfaces permission asks out loud.

## The adaptation (what maps to what)

| ACP need | Existing ORBIS pattern to adapt |
| --- | --- |
| JSON-RPC framing + streamed updates | `a2a/client.py` `dispatch_message_stream` (SSE today → newline-stdio for ACP); reuse the `progress_callback` seam for TTS narration |
| Persistent conversation handle | A2A `contextId` ↔ ACP `sessionId` (same "thread it turn-to-turn" idea) |
| Registering an agent as a target | `agent/delegates.py` — add `type: "acp"` to `Delegate` + `_parse_entry`; `command` + `args` instead of `url` |
| Launch + supervise the child | fleet launcher / `supervise_sidecar` in `src-tauri` (spawn, pipe, reap). For ACP the pipe is bidirectional JSON-RPC, owned by the **Python sidecar** via `asyncio.create_subprocess_exec` |
| Drive a goal to completion by voice | `orchestrate(goal)` / `delegate_to` voice tools — point them at the acp delegate; `session/prompt` is the turn |

## Proposed shape (design, not code)

1. **`type: "acp"` delegate.** A `delegates.yaml` entry like
   `{type: acp, name: claude-code, command: "npx", args: ["@zed-industries/claude-code-acp"]}`.
   The registry parses it like the other two types.
2. **An ACP transport** (`a2a/acp_client.py` or a new `acp/` package): owns the
   subprocess, the `initialize`/`session/new` handshake, an outstanding-request
   map, and a read loop that demultiplexes responses vs. `session/update`
   notifications vs. inbound client-method calls.
3. **Client-method handlers.** `fs/read_text_file` + `fs/write_text_file` map to
   local fs (single-owner — straightforward). `terminal/*` maps to a managed
   subprocess (reuse the launcher patterns). `session/request_permission` is the
   crux (below).
4. **Streaming → voice.** `session/update` chunks feed the existing
   `progress_callback` → TTS, summarized (we don't read raw diffs aloud; we
   narrate "editing payments.py", "running the tests", "3 files changed").
5. **One session per agent**, cached like `contextId`, so follow-ups
   ("now also update the docs") continue the thread.

## Hard questions (the actual spike risk, not the transport)

- **Permission over voice.** `session/request_permission` is synchronous and
  blocking — the agent waits. Voice confirmation ("Claude wants to run
  `rm -rf build/` — approve?") adds latency and is error-prone for risky ops.
  Need a **policy layer**: auto-allow read-only/whitelisted, voice-confirm
  writes/shell, hard-deny destructive patterns. This is a product decision, not
  a protocol one. Likely also a visual fallback in the ORBIS window for diffs.
- **fs / terminal capability scope.** If ORBIS advertises `fs` + `terminal`, the
  agent runs shell + edits files **on the user's machine through ORBIS**. Single-
  owner native makes this defensible, but it's powerful — needs a working-dir
  sandbox/root and an allowlist. Decide whether ORBIS executes terminals itself
  or delegates to the agent's own sandbox.
- **Which binaries.** We don't bundle Claude Code/Codex/etc. — the user installs
  them; we launch what's on PATH (fits the fleet-manifest model: an agent
  manifest could declare `command`/`args`). protoCLI we control.
- **Streaming cadence for TTS.** ACP chunks are token-ish/structured; we must
  buffer to sentence/phrase boundaries before TTS (the same chunking lesson as
  the LLM narration path).
- **Where it runs.** The subprocess + stdio loop belongs in the **Python
  sidecar** (asyncio), beside the A2A client — not Rust — so it shares the
  delegate registry and voice-tool wiring.

## Verdict + recommended first slice

**Worth doing, and cheaper than it looks** because the transport is a variation
on `a2a/client.py` and the registration is one more `type`. The real work is the
permission/fs/terminal service surface and its voice UX.

**Thin vertical to prove it (one spike PR later):** one agent (**OpenCode** or
the **Claude Code** adapter), **text-only over stdio**, no `fs`/`terminal`
advertised, permission = auto-allow-in-dev. Goal: `orchestrate("ask <agent> to
X")` launches the binary, runs `initialize`→`session/new`→`session/prompt`, and
streams `session/update` text back through `progress_callback` to TTS. If that
loop holds, layer on: (2) permission policy + voice confirm, (3) `fs/*`, (4)
`terminal/*`, (5) protoCLI's ACP server adapter.

This composes with the fleet menu bar (ORBIS#325): an ACP coding agent is just
another fleet member ORBIS can launch — its manifest would carry the ACP
`command`/`args`.
