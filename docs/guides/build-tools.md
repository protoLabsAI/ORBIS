# Build a Tool

How to add a new tool the voice agent can call. Two patterns: **sync** (block until done, agent speaks the result) and **async** (return immediately, deliver later — the user can keep chatting).

## Sync tools — quick lookups

Use when: the tool returns in <3 seconds and the user wants the answer right away.

### Steps

1. Pick a name. Add a `FunctionSchema` to `agent/tools.py`.
2. Write the handler.
3. Register it.
4. Add a latency hint.

### Example: a sync `weather` tool

```python
# agent/tools.py

WEATHER_SCHEMA = FunctionSchema(
    name="weather",
    description="Get the current weather for a city. Returns a one-sentence summary.",
    properties={
        "city": {"type": "string", "description": "City name, e.g. 'Tokyo'"},
    },
    required=["city"],
)

async def weather_handler(params: FunctionCallParams) -> None:
    city = params.arguments.get("city", "").strip()
    # ... do the work, e.g. call a weather API ...
    result = f"It's 72 and partly cloudy in {city}."
    await params.result_callback(result)   # ← THE result. LLM speaks this.
```

Add to the registry inside `register_tools()`:

```python
llm.register_function(
    "weather",
    _wrap_sync(weather_handler),
    cancel_on_interruption=True,    # interrupt cancels the call
)
standard.append(WEATHER_SCHEMA)
```

And register a latency tier in `TOOL_LATENCY` near the top of `agent/tools.py`:

```python
TOOL_LATENCY: dict[str, Latency] = {
    ...
    "weather": Latency.MEDIUM,    # 0.5-3s expected
}
```

That's it. The LLM will:
1. Emit a brief preamble ("hmm, let me check") via the [TOOL USE prompt block](/explanation/natural-fillers#the-pre-tool-preamble)
2. Call `weather(city="Tokyo")`
3. Get the result back
4. Speak the answer

## Async tools — long-running investigations

Use when: the tool takes many seconds (or minutes) and the user shouldn't have to wait silently.

### The critical pattern

::: danger Never call `result_callback` in the foreground for async tools.
Pipecat treats whatever you pass to `result_callback()` as the **finished** result. If you call it in the foreground with a placeholder ("I'll get back to you"), the LLM thinks the tool is done with that as the answer — and will fabricate follow-up data about the topic.
:::

The correct shape: do nothing in the foreground, spawn a background task, call `result_callback` with the **real** result when the work completes.

### Example: an async `deep_dive` tool

```python
# agent/tools.py

DEEP_DIVE_SCHEMA = FunctionSchema(
    name="deep_dive",
    description=(
        "Kick off a long-running investigation (30s+). Use when the user "
        "doesn't need an immediate answer — they can keep chatting and "
        "the agent will share the result when it's ready."
    ),
    properties={
        "topic": {"type": "string", "description": "What to investigate"},
    },
    required=["topic"],
)

async def deep_dive_handler(params: FunctionCallParams) -> None:
    topic = params.arguments.get("topic", "")
    logger.info(f"[deep_dive] starting: {topic!r}")

    # NOTHING in the foreground. Pipecat auto-injects a "tool is running"
    # placeholder for the LLM context. The LLM's preamble is what the
    # user heard immediately ("alright, give me a few minutes").

    async def _background() -> None:
        result = await actually_do_the_research(topic)   # 30+ seconds
        # NOW call result_callback. Pipecat injects this as a developer
        # message and the LLM speaks the actual answer at the next
        # natural opportunity (typically the next user-silence).
        await params.result_callback(result)
        logger.info(f"[deep_dive] result_callback fired ({len(result)} chars)")

    asyncio.create_task(_background())
```

Register it with `cancel_on_interruption=False` (this is what makes it async):

```python
llm.register_function(
    "deep_dive",
    deep_dive_handler,                # NOTE: not wrapped in _wrap_sync
    cancel_on_interruption=False,      # ← async path; pipecat treats as deferred
)
standard.append(DEEP_DIVE_SCHEMA)
```

Mark it as async + slow in the metadata:

```python
ASYNC_TOOL_NAMES: frozenset[str] = frozenset({"slow_research", "deep_dive"})

TOOL_LATENCY: dict[str, Latency] = {
    ...
    "deep_dive": Latency.SLOW,    # 3s+ expected, generated progress fires
}
```

### What happens at runtime

1. User asks "can you do a deep dive on quantum computing when you have a moment?"
2. LLM emits inline preamble ("alright, give me a few minutes") — spoken immediately via the [TOOL USE prompt block](/explanation/natural-fillers#opening-preamble-inline)
3. LLM calls `deep_dive(topic="quantum computing")`
4. Handler returns immediately; pipecat marks the call `in_progress`
5. User keeps chatting — agent answers normally
6. **30 seconds later**: background task finishes, calls `result_callback(real_result)`
7. Pipecat injects a developer message with the result
8. LLM generates a response speaking the real answer; pipeline streams it through TTS at the next natural pause

## Latency tiers

Each tool registers a tier that gates filler behavior:

| Tier | When | Behaviour |
|:---|:---|:---|
| `FAST` | <500ms | No preamble, no progress. The answer arrives faster than any filler could. (calculator, get_datetime) |
| `MEDIUM` | 0.5-3s | Inline preamble + answer. (web_search, deep_research, a2a_dispatch, weather) |
| `SLOW` | 3s+ | Inline preamble + periodic generated progress lines until the result lands. (slow_research, deep_dive) |

See `agent/tools.TOOL_LATENCY` for the registration table.

## Hand-off as a delegate (not a tool)

If your "tool" is actually delegating a question to another agent OR a heavier model, **don't write a new tool** — add it to `config/delegates.yaml`. The agent already has `delegate_to(target, query)` which handles both A2A agents and OpenAI-compat endpoints. You get the right behaviour for free, and the LLM picks based on the delegate's description (no prompt engineering required).

```yaml
delegates:
  - name: my_specialist
    description: "What this delegate is good at."
    type: a2a            # or openai
    url: ...
```

See [Delegates](/reference/delegates) for the full schema. Add a new specialist agent or a new heavier model — same tool, same code path.

## Conventions

- **Handler is `async def`** — even sync tools (the LLM call is async-only).
- **Handler signature is `(params: FunctionCallParams) -> None`** — return value ignored; speak via `result_callback`.
- **Sync handlers MUST be wrapped with `_wrap_sync()`** in `register_tools()` — that wrapper handles the `on_finish` hook (cancels the SLOW progress loop). Async handlers do NOT use the wrapper because their work continues after the function returns.
- **Catch your own connection errors** — see how `delegate_to` catches `httpx.ConnectError` to surface a friendly spoken message rather than crash the turn (see [`agent/tools.py`](https://github.com/protoLabsAI/protoVoice/blob/main/agent/tools.py)).
- **Tool args feed the inline preamble**. The TOOL USE prompt instructs the LLM to ground its preamble in the topic, so naming args clearly (`query`, `city`, `topic`) helps natural acknowledgement.
- **Don't mention tool names in persona prompts.** The LLM picks tools from the schemas, not from prompt re-statements. A persona that says "use `web_search` for X" goes stale the moment you rename or remove that tool. Write personas that describe behavior — the schemas tell the LLM what's available.

## Common pitfalls

- **Calling `result_callback` early in async tools.** See the danger callout above. The LLM will hallucinate follow-ups.
- **Forgetting `ASYNC_TOOL_NAMES`.** If you register `cancel_on_interruption=False` but don't add the name to `ASYNC_TOOL_NAMES`, the progress loop keeps firing forever (the `on_finish` cancel hook never fires for async tools).
- **Forgetting `TOOL_LATENCY`.** Defaults to MEDIUM, which is usually fine — but FAST tools without an explicit hint will get a useless preamble for `15 * 1.2 + 3`.
- **Not catching backend errors.** Tools that throw raw exceptions break the LLM turn (pipecat surfaces an ErrorFrame). Catch and convert to a spoken error string via `result_callback`.
- **Verbose results.** The LLM speaks the raw `result_callback` payload. Keep it short (1-3 sentences) or instruct the LLM to summarize via the persona prompt.
