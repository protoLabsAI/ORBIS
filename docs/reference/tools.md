# Tools

Every tool registered on the voice LLM. Adding new tools? See the **[Build a Tool](/guides/build-tools)** guide first — it covers the sync vs async patterns and the most expensive footgun (`result_callback` semantics for async tools).

Sync tools block the LLM loop until they return; async tools return to the LLM immediately and pipecat injects the result as a developer message when ready.

Source: [`agent/tools.py`](https://github.com/protoLabsAI/protoVoice/blob/main/agent/tools.py).

## Patterns at a glance

| | Sync (`cancel_on_interruption=True`) | Async (`cancel_on_interruption=False`) |
|:---|:---|:---|
| Foreground call | `await result_callback(real_result)` at the end of the handler | **Do NOT** call result_callback in the foreground |
| Background work | None | `asyncio.create_task(...)` does the work, calls result_callback at the end |
| User experience | Inline preamble → tool runs → answer streams | Inline preamble → tool kicks off → user keeps chatting → result lands later |
| Latency tier | `FAST` or `MEDIUM` | `SLOW` |
| Filler progress | Generated narration if `SLOW` | n/a (handler returns immediately) |
| Interrupt cancels work | Yes | No |

::: danger Async-tool gotcha
For async tools, **never** call `result_callback` in the foreground with a placeholder ("I'll get back to you"). Pipecat treats it as the **finished** result — the LLM will think the tool returned that string as the actual answer and fabricate follow-ups about the topic.

Spawn an asyncio task. Have the task call `result_callback` with the **real** result when it finishes. The user's "let me look into it" comes from the LLM's [inline preamble](/explanation/natural-fillers#the-pre-tool-preamble), not from the tool.
:::

## Sync tools

### `calculator`

Safe AST-based arithmetic evaluation. Supports `+ - * / // % **` and unary minus. No attribute access, no function calls.

```json
{ "expression": "15 * 1.2 + 3" }
```

Returns a natural-language sentence the LLM can speak verbatim: `"15 * 1.2 + 3 equals 21."`

### `get_datetime`

Returns the current time in the container's configured timezone (default `America/New_York`, override via `TZ`).

```json
{}
```

### `web_search`

DuckDuckGo via the `ddgs` package. Returns up to 5 snippets concatenated into a single string, capped at 2000 characters so the LLM context stays sane.

```json
{ "query": "history of hot dogs" }
```

### `delegate_to`

Single hand-off tool covering both A2A agents and OpenAI-compat LLM endpoints. The LLM picks a target by name from the `enum`-restricted choices in the schema, which are populated dynamically from `config/delegates.yaml` at session start.

```json
{ "target": "ava", "query": "give me a sitrep on the dashboard project" }
```

Each delegate's `description` is baked into the tool's schema description — that's how the LLM knows which target fits which question. See [Delegates](./delegates) for full details + adding new targets.

`delegate_to` is **only registered** when `config/delegates.yaml` has at least one entry. Empty file → no tool, no chance the LLM tries to call something that doesn't exist.

## Async tools

### `slow_research`

Long-running investigation. LLM acknowledges immediately ("Sure — I'll look into that..."); a background asyncio task runs for `SLOW_RESEARCH_SECS` seconds, then pushes the result via the DeliveryController with `NEXT_SILENCE` policy.

```json
{ "query": "history of hot dogs" }
```

Keywords for `when_asked` matching are derived from words in the query longer than 3 chars.

## How tool selection actually works

The LLM picks tools entirely from the **schemas** — not from prompt re-statements. Each tool's `description` is the source of truth.

This is why persona prompts (SOUL.md, skill YAMLs) **should not hardcode tool names**. If your `web_search` tool gets renamed, every prompt that mentions it goes stale. Instead, write personas that describe behavior:

> "When a question needs current information, reach for the tools available rather than guessing."

The LLM sees the actual tool list (with descriptions) every turn via the OpenAI function-calling contract. That's enough — strong models pick correctly without prompt repetition. If you find a model under-using tools, tighten the tool's own description first; only fall back to prompt-level reinforcement if that doesn't work.

For the dynamic delegate enumeration (`delegate_to`'s `target` enum), that's built at session start from `config/delegates.yaml` — see [Delegates](./delegates).

## Adding a new tool (`@tool` decorator)

Most tools only need a schema, a handler, and a latency tier. The `@tool` decorator in `agent/tools.py` registers all three at import time — no edits to `register_tools`, no entry in a separate latency dict.

```python
from pipecat.services.llm_service import FunctionCallParams
from agent.filler import Latency
from agent.tools import tool

@tool(
    "weather",
    "Current weather for a city. Use when the user asks about weather.",
    parameters={
        "city": {"type": "string", "description": "City name, e.g. 'Portland, OR'"},
    },
    required=["city"],
    latency=Latency.MEDIUM,   # drives progress-narration cadence
)
async def weather_handler(params: FunctionCallParams) -> None:
    city = params.arguments.get("city", "").strip()
    # ... fetch weather, format response ...
    await params.result_callback(f"It's 62°F and overcast in {city}.")
```

Drop that module where it gets imported at process boot and the tool shows up automatically in both voice and A2A/text paths. Skill `tools:` allowlists pick it up by name on next connection.

### Async tools

Set `async_tool=True` for tools that schedule background work and call `result_callback` later. Pipecat treats async tools as `cancel_on_interruption=False` — the deferred result gets injected back into the LLM context on completion.

The `slow_research` built-in shows the pattern: the decorator-registered handler is a placeholder, and `register_tools` substitutes the real handler that closes over the session's `DeliveryController`. If your async tool needs similar per-session context, follow the same substitution pattern inside `register_tools`.

### Tools that need runtime context

`delegate_to` closes over the per-session delegate registry (which may be filtered by the active skill). It stays hand-wired in `register_tools` rather than using `@tool`, because the decorator-time schema can't express "target enum = whatever the live registry says." If your new tool needs similar runtime context, keep it hand-wired and reference `delegate_to` as the template.
