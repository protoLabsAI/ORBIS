# Agent & LLM

The brain: the language model that routes and replies, the sub-agents it can
delegate to, and the actions it can take. Set these in **Settings → Agent**
(and in [`orbis.yaml`](./config) for headless setups).

For the *why*, see [The agent model](/explanation/the-agent-model).

## The LLM

One small, fast model powers the orb's routing and personality — it decides
each turn and writes the replies. Pick a **provider** in **Settings → Agent →
LLM**.

| Provider | Local? | Notes |
| --- | --- | --- |
| **Built-in (MLX)** | ✅ | Apple-Silicon native (Qwen 3.5 4B) in-process. Best local quality on M-series; ~2.5 GB first download. |
| **Ollama** | ✅ | A separate local process; share a model with other tools. |
| **LM Studio** | ✅ | Local; start the LM Studio server first. |
| **vLLM** | ✅ | Your own local vLLM server. |
| **protoLabs** | ☁️ | The protoLabs gateway — fast, voice-tuned routing. |
| **OpenAI** | ☁️ | `gpt-4o-mini`, a few cents/hour. |
| **Anthropic** | ☁️ | Claude Haiku — great personality. |
| **Groq / DeepSeek** | ☁️ | Fast / cheap alternatives. |

**Keys** (`llm:` in `orbis.yaml`; overrides the `LLM_*` env vars):

| Key | Meaning |
| --- | --- |
| `url` | OpenAI-compatible base URL (or `mlx://…` / `ollama` local). |
| `model` | Model id. |
| `api_key` / `api_key_env` | The key directly, or the name of an env var holding it. |

### Advanced LLM options

All optional; leave unset for simple single-model behaviour.

- **Failover backup** (`fallback:`) — if the primary LLM errors (e.g. the cloud
  gateway drops), ORBIS switches to a backup for the rest of the session so the
  orb keeps talking. The natural backup is a **local** model. Same fields as the
  primary.
- **Two-model routing** (`router_model` / `content_model`) — use a stronger
  model for the tool-decision turn and a faster one for narrating a tool/delegate
  result. Both served at the same `url`. OpenAI-compatible gateways only.
- **Micro-task model** (`micro_model`) — the cheap tier for throwaway generation
  (fillers, acknowledgements, proactive phrasing). Defaults to `model`.

## Delegates

**Delegates** are sub-agents the orb can hand work to. The LLM picks one by its
**description** when it decides delegation is the right call. Manage them in
**Settings → Agent → Delegates**.

Three types:

| Type | What it is |
| --- | --- |
| **A2A agent** | A JSON-RPC fleet peer (the Agent2Agent protocol) — e.g. another studio agent. |
| **OpenAI-compat** | Any `/v1/chat/completions` endpoint treated as a sub-agent. |
| **ACP coding agent** | A local terminal coding agent ORBIS launches and drives over the [Agent Client Protocol](https://agentclientprotocol.com) — protoCLI, OpenCode, Claude Code, Codex. |

**Fields:**

| Field | Applies | Meaning |
| --- | --- | --- |
| **Name** | all | Used in `delegate_to(target=…)`. Lowercase, no spaces. |
| **Description** | all | The LLM reads this to choose between delegates — be specific. |
| **URL** | A2A · OpenAI | A2A: the JSON-RPC endpoint (often `/a2a`). OpenAI: the base URL ending in `/v1`. |
| **Auth scheme** | A2A | `apiKey` (X-API-Key) or `bearer`, or none for a public endpoint. |
| **Credentials env var** | A2A | Name of the env var holding the secret (set it in `.env`). |
| **Model** | OpenAI | Provider model id. |
| **API key env var** | OpenAI | Optional; for endpoints that need auth. |
| **Command** | ACP | The agent binary to launch (e.g. `proto`, `opencode`). |
| **Args** | ACP | Args that start it in ACP mode (e.g. `["--acp"]`, `["acp"]`). |
| **Workdir** | ACP | The directory the agent is responsible for — its session working directory. |

No secrets cross the wire from the UI — the schema only references env-var
**names**; the values come from the process environment.

### ACP coding agents

An **ACP delegate** is a coding agent that runs on *your* machine. ORBIS launches
it (`command` + `args`) in the registered `workdir`, drives it by voice, narrates
its tool calls ("Editing app.py", "Running pytest"), and the agent reads, edits,
and runs code **in that directory**. The `workdir` is the unit of identity — add
the same agent twice against two repos and you get two scoped delegates
(*"ask proto in the ORBIS repo…"*).

Launch commands (each speaks ACP over stdio):

| Agent | `command` | `args` |
| --- | --- | --- |
| **protoCLI** | `proto` | `["--acp"]` |
| **OpenCode** | `opencode` | `["acp"]` |
| **Claude Code** | `npx` | `["@zed-industries/claude-code-acp"]` |
| **Codex** | `codex-acp` | `[]` (install [zed-industries/codex-acp](https://github.com/zed-industries/codex-acp)) |

The agent must be installed and on your `PATH`. ORBIS only launches one on an
explicit request — never automatically.

See [Add a delegate](/how-to/add-a-delegate) and
[Voice-drive a coding agent](/how-to/voice-drive-a-coding-agent) for the steps.

## What the agent can do

Beyond replying, the orb can take actions by voice:

- **Reminders** — set, list, and cancel time-based reminders (one-off or
  recurring). They also appear in the top-right **reminders bell**.
- **Delegate** — hand a task to one configured delegate and speak the result.
- **Orchestrate** — run a bounded multi-step task across your delegates in the
  background, then report back.

## See also

- [The agent model](/explanation/the-agent-model)
- [Add a delegate](/how-to/add-a-delegate)
- [Config reference](./config)
