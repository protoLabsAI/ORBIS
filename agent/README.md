# Agent extension points

The voice agent has a deliberately small surface. The two things you'll extend
are **tools** (functions the model can call) and **delegates** (sub-agents it
hands work to).

## Add a tool

Tools **self-register** via the `@tool` decorator — write the function, decorate
it, done. No list to edit, no dispatch to wire.

```python
from .filler import Latency
from pipecat.services.llm_service import FunctionCallParams

@tool(
    "set_brightness",
    "Set the display brightness. Use when the user asks to dim/brighten the screen.",
    parameters={
        "level": {"type": "integer", "description": "0–100 percent."},
    },
    required=["level"],
    latency=Latency.FAST,   # hint so the filler picks the right ack; see filler.py
)
async def set_brightness_handler(params: FunctionCallParams) -> None:
    level = int(params.arguments.get("level") or 50)
    # ...do the work...
    await params.result_callback(f"Set brightness to {level} percent.")
```

The decorator records a `ToolSpec` in `_TOOL_REGISTRY`; `register_tools()` builds
the LLM schema from it. Read the function args from `params.arguments`; return
the spoken result through `await params.result_callback(...)`.

Keep tools genuinely useful and few — ORBIS's pitch is "delegate the heavy
lifting", not "grow a tool zoo". If a capability belongs to another agent,
expose it as a **delegate** instead.

## Add a delegate (an instance)

A delegate is config, not code. Add an entry to `config/delegates.yaml` (or use
**Settings → Agent → Delegates** in the app) with a name, type, description, and
URL. The `delegate_to` tool picks it up automatically — the model chooses among
delegates by their **description**, so make that specific. Full walkthrough:
[docs/how-to: add a delegate](../docs/how-to/add-a-delegate.md).

## Add a delegate _type_ (a protocol adapter)

To support a new agent protocol (beyond the built-in A2A / OpenAI-compat / ACP),
add an adapter in [`delegate_adapters.py`](./delegate_adapters.py): subclass the
`DelegateAdapter` interface (config schema, parse, validate, dispatch, probe) and
register it at module load:

```python
register_adapter(MyProtocolAdapter())
```

The fastest path is to copy an existing adapter as a template — once registered,
the new type lights up everywhere: YAML parsing, the Settings UI form (the form
is generated from each adapter's config schema), health probes, and the
`delegate_to` tool.

## The seam

Tools and adapters import a thin set of agent-local helpers (`Latency`,
`FunctionCallParams`, the delegate facades) — they don't reach deep into core
internals, so adding one stays self-contained. Keep it that way.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the big picture.
