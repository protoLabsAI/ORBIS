# Personas & Skills

A **skill** is a named voice persona — system prompt, TTS voice, LLM tuning parameters. The default persona is defined by `config/SOUL.md`; alternative personas live in `config/skills/*.yaml`.

## The dropdown

On the main page, pick a skill from the dropdown and click **Start**. The dropdown takes effect at the *next* connection — not mid-session, because we'd have to tear down and rebuild the Pipecat pipeline to swap the TTS voice cleanly. Snapshot-at-connect matches how users reason about it anyway.

## Writing a skill YAML

```yaml
# config/skills/tutor.yaml
slug: tutor
name: Tutor
description: |
  A patient tutor who answers questions with follow-up nudges.

system_prompt: |
  You are a friendly tutor. Answer questions in one or two sentences,
  then ask one gentle follow-up to check the user's understanding.
  Never lecture. Don't use markdown — you're speaking aloud.

tts_backend: kokoro       # 'kokoro' or 'fish'
voice: af_nicole          # Kokoro voice id OR Fish reference_id
lang: a                   # kokoro language code (optional)
temperature: 0.6
max_tokens: 120
filler_verbosity: brief   # overrides session verbosity on connect (optional)
```

### `system_prompt` vs `system_prompt_file`

Inline prompts stay in-YAML. For longer prompts, point at a file:

```yaml
system_prompt_file: tutor.md
```

The path is relative to the YAML file.

### Inheritance (`extends:`)

A skill can inherit from another — every field is merged from the parent, child keys win. When `extends:` is omitted, the parent defaults to `default` (the SOUL.md persona). Set `extends: null` to opt out.

```yaml
# config/skills/voice-01.yaml
slug: voice-01
name: Voice 01
voice: voice_01
# tts_backend + system_prompt inherited from the default skill
```

This is how the voice-clone YAMLs stay three lines each — they pick up the SOUL.md persona + Fish backend from the default without repeating them. Define a new voice by dropping in a YAML with just `slug`, `name`, and `voice`; everything else cascades.

Chains work: `extends: josh` would inherit from `josh`, which itself inherits from `default`. Cycles are detected and warned.

### Tool restriction

```yaml
tools: [calculator, get_datetime, web_search]
```

When set, the skill's session only sees the listed tools — the LLM literally can't call anything else because the others aren't in its `ToolsSchema`. Empty list (default) = expose every registered tool.

Useful for keeping personas in lane: a kitchen-savvy `chef` skill doesn't need `a2a_dispatch` or `slow_research`; a customer-support skill might want only `web_search` + a `lookup_order` tool you've added.

Unknown tool names in the list are logged as warnings and ignored. If your list filters down to zero tools, protoVoice refuses to leave the agent toolless and falls back to exposing all (with a warning).

### Per-skill behavior tuning

Pipeline controllers (backchannel, micro-ack, barge-in) default to the values set via environment variables. A skill can override them with a `behavior:` block. Each key accepts `false` (disable), `true` (enable with defaults), or a dict with timing overrides.

```yaml
behavior:
  backchannel: false              # silent skill — no listener-acks
  micro_ack:
    first_ms: 800                 # wait longer before injecting "mm"
  bargein:
    grace_ms: 500                 # longer grace — harder to interrupt
```

| Key | Shape | Effect |
|---|---|---|
| `backchannel` | `false` / `true` / `{enabled, first_ms, interval_ms}` | Mid-turn "mm-hmm" emission. Disable for "quiet" personas. |
| `micro_ack` | `false` / `true` / `{enabled, first_ms}` | Short ack if pipeline hasn't spoken within `first_ms` of UserStopped. |
| `bargein` | `false` / `true` / `{enabled, grace_ms}` | Adaptive barge-in gate during bot speech. `false` lets every VAD hit interrupt. |

Behavior overrides are session-scoped and read at connection time. Settings from the parent skill (via `extends:`) are merged per-controller — a child that only overrides `backchannel` keeps the parent's `bargein` + `micro_ack`.

### Routing to a different LLM per skill

By default every skill hits the process-wide `LLM_URL` (typically the local vLLM). A skill can override with an `llm:` block:

```yaml
llm:
  url: http://gateway:4000/v1     # LiteLLM / OpenRouter / direct provider
  model: anthropic/claude-sonnet-4-6
  api_key_env: LITELLM_KEY
```

| Key | Purpose |
|---|---|
| `url` | OpenAI-compatible base URL for this skill's chat completions |
| `model` | Model name sent to that endpoint |
| `api_key_env` | Env var to read the API key from |
| `extra_body` | Optional — passed through as OpenAI `extra_body`. Omit to let the gateway run its own defaults. |

When `llm.url` is set, protoVoice:
- Drops the vLLM-specific `chat_template_kwargs` body (they get rejected by non-vLLM gateways) unless you explicitly pass `extra_body`.
- Keeps the `developer` role intact (vLLM rejects it; OpenAI-shaped gateways generally accept it).

`temperature` and `max_tokens` stay at the skill's top level — they apply to both the default and custom endpoints.

### Restricting delegates per skill

By default every delegate in `config/delegates.yaml` is exposed through the `delegate_to(target=…)` tool. A skill can filter:

```yaml
delegates: [ava, opus]   # this skill can only delegate_to these two
```

Empty list / omitted = full registry exposed (current behavior). Unknown names are dropped silently so a typo doesn't leave the skill with zero delegates — just with the ones that resolved.

### Dedicated orb viz

A skill can ship its own orb visualization — variant, palette, or individual params — and the client auto-applies it when the user switches to that skill.

```yaml
viz:
  variant: fractal       # orb variant name — see orb-visualizer reference
  palette: Ember         # palette name within that variant
  params:                # individual overrides on top of the palette
    density: 2.4
    speed: 0.45
```

- All three keys are optional; omit the block entirely if the skill should leave the orb alone.
- Unknown `variant` or `palette` names are logged as a console warning on the client and skipped — the params still apply.
- Inherited via `extends:` — child skills can shadow `viz.palette` while keeping the parent's `variant`.

A per-user `pinned_viz` in the roster overrides this block (priority: `user.pinned_viz → skill.viz → nothing`). See [Users → Pinning](./users#pinning).

## The default persona — `SOUL.md`

`config/SOUL.md` is the default skill. Plain markdown, no frontmatter. Edit freely — changes take effect on server restart. The default ships with a chief-of-staff-ish tone and directives about when to dispatch to other agents.

## TTS voice choice per skill

- `tts_backend: kokoro` with `voice: af_heart` (or any Kokoro preset) — low-latency, fixed voice. Good for skills where character matters less than speed.
- `tts_backend: fish` with `voice: <reference_id>` — high-quality, cloneable. The reference must already be saved on the Fish server (see [Clone a Voice](./clone-a-voice)).

The skill's `tts_backend` overrides the session-level `TTS_BACKEND` env for that connection. So a `chef` skill can speak in Kokoro even when the session default is Fish, and vice versa.

## Switching via the API

```bash
curl -X POST http://localhost:7867/api/skills \
  -H "Content-Type: application/json" \
  -d '{"slug":"chef"}'
# → {"active":"chef"}
```

## Programmatic iteration

```bash
curl http://localhost:7867/api/skills | jq .
```

Returns the active slug and the full skill list with names + descriptions.
