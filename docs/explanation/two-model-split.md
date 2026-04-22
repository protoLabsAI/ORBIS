# Two-Model Split

protoVoice runs the in-pipeline LLM as a fast **router** and offloads heavy reasoning to one or more **delegates**. The router stays in the critical path of every turn; delegates only fire when the router calls `delegate_to`.

## The split

- **Router** — the local OpenAI-compatible LLM at `LLM_URL` (typically a small fast model: Qwen3.5-4B / 9B, GPT-4o-mini, Llama 3 8B). Handles every user turn: chitchat, tool selection, the inline preamble before tool calls, and speaking the final answer.
- **Delegates** — a list of heavier "second-tier" backends: other agents in the protoLabs fleet (via A2A) and/or larger LLMs on remote endpoints. Configured in `config/delegates.yaml`. Invoked only when the router decides a question warrants the hand-off.

This pattern matters because:

- **TTFA**. The router is in the critical path of every turn; latency is felt directly. Tiny models do 150-300 tok/s locally; big models do 30-50. Don't put a big model in the router slot.
- **Cost**. Routing chitchat to a tiny local model burns zero API tokens. Only research questions hit a delegate.
- **Latency isolation**. A delegate can take 2-30 s without blocking the conversation, because it runs behind a tool call that the user hears acknowledged immediately via the [inline preamble](/explanation/natural-fillers).

## Delegate types

`config/delegates.yaml` accepts two kinds:

### `type: a2a` — another agent in the fleet

```yaml
- name: ava
  description: "Chief of staff — sitreps, planning, fleet delegation."
  type: a2a
  url: ${AVA_URL:-http://ava:3008/a2a}
  auth: { scheme: apiKey, credentialsEnv: AVA_API_KEY }
```

A2A delegates are full agents with their own tools, memory, and subagents. Strongest answers; one extra hop per call.

### `type: openai` — direct LLM endpoint

```yaml
- name: opus
  description: "Heavy reasoning model — analysis, summarization, depth."
  type: openai
  url: http://gateway:4000/v1
  model: claude-opus-4-6
  api_key_env: LITELLM_MASTER_KEY
```

OpenAI-compat delegates are one-shot chat completions to any LiteLLM / cloud / self-hosted endpoint. Smaller moving part; just "raw model with a research-flavored system prompt." Best for pure depth-not-tools questions.

You can mix as many of each as you want. The router LLM picks the right one per question based on each delegate's `description`.

## How target selection works

`delegate_to`'s schema is built dynamically at session start. The `target` field is `enum`-restricted to known delegate names; the tool description enumerates each delegate's `description`. The LLM picks based on those descriptions.

So for a fleet with both `ava` and `opus` configured, the LLM sees something like:

```
delegate_to: Hand off a question to a specialized backend...
Available targets:
  - ava: Chief of staff for the protoLabs fleet — sitreps, planning...
  - opus: Heavy reasoning model — analysis, summarization, depth...
```

User asks "what's the status of the dashboard project?" → router picks `ava`. User asks "summarize the differences between two architectures I'll describe" → router picks `opus`. The split is implicit in the descriptions.

## Why not just one model

One good model at every turn is simpler and produces marginally better individual answers. It's also 3-5× more expensive and 2-10× slower at steady state. For a voice agent where the user is listening live, the latency tax is unacceptable for routine turns.

If gateway latency drops below ~300 ms TTFB and local inference becomes free, the split collapses. We're not there yet.

## Health check

```bash
curl http://localhost:7867/healthz | jq .delegates
# [
#   {"name": "ava",  "type": "a2a"},
#   {"name": "opus", "type": "openai"}
# ]
```

## Code references

- `agent/delegates.py` — `Delegate` dataclass, `DelegateRegistry`, `dispatch()` (branches on type)
- `agent/tools.py::_delegate_to_handler` — the tool binding
- `a2a/client.py::dispatch_message` — A2A wire-protocol implementation
