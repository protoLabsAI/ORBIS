# Delegates

The voice agent has one delegation tool — `delegate_to(target, query)` — that hands off heavy questions to either:

- **A2A agents** in the protoLabs fleet (ava, quinn, etc.)
- **OpenAI-compatible LLM endpoints** (LiteLLM gateway, OpenAI itself, OpenRouter, anything compat)

Both go through the same tool. The LLM picks a target by name based on the description in the tool schema. Add new delegates by editing `config/delegates.yaml` — no code change.

## File location

Default `config/delegates.yaml`. Override with `DELEGATES_YAML=<path>`.

## Schema

```yaml
delegates:
  # ─── A2A agent ──────────────────────────────────────────────────────
  - name: ava
    description: >-
      Chief of staff for the protoLabs fleet. Best for project status
      reports, coordination across other agents, planning a feature.
    type: a2a
    url: ${AVA_URL:-http://ava:3008/a2a}
    auth:
      scheme: apiKey                   # or "bearer"
      credentialsEnv: AVA_API_KEY

  # ─── OpenAI-compatible LLM endpoint ─────────────────────────────────
  - name: opus
    description: >-
      Heavy reasoning model. Use when the user wants analysis,
      summarization, or a thorough explanation.
    type: openai
    url: http://gateway:4000/v1
    model: claude-opus-4-6
    api_key_env: LITELLM_MASTER_KEY
    max_tokens: 400                    # default 400
    temperature: 0.4                   # default 0.4
    system_prompt: >-                  # optional override
      You are a research assistant. Answer thoroughly but concisely.
```

## Field reference

### Common

| Field | Required | Notes |
|:---|:---|:---|
| `name` | yes | Identifier the LLM uses in `delegate_to(target=...)`. Lower-snake_case recommended. |
| `description` | yes | The LLM reads this to decide whether the target fits the user's question. Be specific about what the delegate is good at. |
| `type` | yes | `a2a` or `openai` |
| `url` | yes | Endpoint URL. Supports `${VAR}` and `${VAR:-default}` env expansion. |

### `type: a2a`

| Field | Notes |
|:---|:---|
| `auth.scheme` | `apiKey` (sent as `X-API-Key`) or `bearer` (sent as `Authorization: Bearer`) |
| `auth.credentialsEnv` | Env var holding the credential value |
| `headers` | Extra static headers (e.g., `a2a-extensions`) |

### `type: openai`

| Field | Default | Notes |
|:---|:---|:---|
| `model` | required | Model name at the endpoint |
| `api_key_env` | unset | Env var for the bearer token |
| `max_tokens` | 400 | Cap per response |
| `temperature` | 0.4 | Sampling temperature |
| `system_prompt` | built-in research-assistant prompt | Override to change tone/length |

## How the LLM picks a target

At session start, `delegate_to`'s schema is built dynamically:

- `target` is `enum`-restricted to known delegate names (the LLM literally can't pass an unknown one)
- The tool description enumerates each delegate's `description`

So a session-start prompt looks (in part) like:

```
delegate_to: Hand off a question to a specialized backend...
Available targets:
  - ava: Chief of staff for the protoLabs fleet. Best for project status...
  - opus: Heavy reasoning model. Use when the user wants analysis...
```

The LLM picks based on those descriptions. **Write them like onboarding docs for the LLM.**

## Adding a delegate

1. Edit `config/delegates.yaml` — add the entry
2. Set any env vars referenced in `credentialsEnv` / `api_key_env` / `${VAR}` substitutions
3. Restart the server

::: tip Persona prompts should NOT mention delegates by name.
The delegate list is dynamic — it changes every time you edit the YAML. If a persona prompt hardcodes "use the `ava` delegate for X," you have to update prompts every time delegates change. Let the schema do its job — the LLM sees the available delegates and their descriptions in `delegate_to`'s definition without any prompt repetition.
:::

Verification:

```bash
curl http://localhost:7867/healthz | jq .delegates
# [
#   {"name": "ava",  "type": "a2a"},
#   {"name": "opus", "type": "openai"}
# ]
```

## Outbound A2A wire format (for `type: a2a`)

`POST {url}`:

```json
{
  "jsonrpc": "2.0",
  "id": "<uuid>",
  "method": "message/send",
  "params": {
    "contextId": "<uuid>",
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "<query>"}]
    }
  }
}
```

We extract the first `text` part from the first artifact in the response. Errors (HTTP non-2xx, JSON-RPC error, missing artifact) propagate to the LLM as a spoken error message rather than crashing the turn.

## Limits

- **Synchronous only.** One HTTP request per call. Long delegations block the tool slot until they return.
- **No retry / backoff.** First failure is the spoken error.
- **No streaming.** Responses arrive in full.

These are conscious choices for v1. The async/streaming path is `slow_research`.

## Restricting per-skill

Skills can opt out of `delegate_to` (or any tool) by listing their allowed tools:

```yaml
# config/skills/chef.yaml
tools: [get_datetime, calculator, web_search]   # no delegate_to
```

See [Personas & Skills](/guides/personas-and-skills#tool-restriction).
