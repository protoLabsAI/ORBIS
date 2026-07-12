# Personas — drop-in identity bundles

*Plan of record for epic #611 (phases #607–#610). Drafted 2026-07-11 from
a research pass over protoVoice's skills mechanic and ORBIS's current
single-persona loader.*

## What this is

A **persona** is everything that makes the orb *someone*: system prompt,
TTS voice, optional LLM override, orb visual, tuning knobs. Today ORBIS
has exactly one, assembled from `config/orbis.yaml` (+ optional
`config/persona.md` prompt file) by `agent/persona.py` — which is
explicitly the single-persona replacement for the skills catalog the
seed inherited from protoVoice.

This epic brings the catalog back, upgraded: each persona is **one
drop-in frontmatter-markdown file**. YAML frontmatter carries the
identity metadata; the markdown body *is* the system prompt. No
YAML-plus-prompt-file pairs, no core edits to add one.

## Provenance — what protoVoice got right

protoVoice's `skills/{loader,models}.py` + `config/skills/*.yaml` is the
direct ancestor (ORBIS forked at v0.12.1). Worth porting as-is:

- **A skill is the full identity bundle** — slug, name, description,
  prompt, `tts_backend`/`voice`, `temperature`/`max_tokens`,
  `filler_verbosity`, per-skill `llm:` endpoint override, `tools:` /
  `delegates:` filters, and `viz:` (orb variant/palette/params) that
  clients auto-apply on switch.
- **`extends:` inheritance** with the default persona as the implicit
  parent. A Fish voice-clone skill is four lines (slug/name/description/
  voice) and inherits everything else. Cycle detection; one-level deep
  merge for `llm`/`behavior`/`viz`.
- **Catalog endpoints** — `GET /api/skills` (catalog + active),
  `POST /api/skills` (persisted selection), `POST /api/skills/reload`.

What does *not* port: protoVoice applies the skill **at session
connect** (snapshot). ORBIS's native pipeline is persistent — there is
no reconnect. Switching must be a live hot-swap (see below). Per-user
`allowed_skills` scoping also drops — ORBIS is single-user.

## The format — `personas/<slug>.md`

```markdown
---
name: Chef Bruno
description: Italian-American chef, practical kitchen wisdom.
extends: orbis            # optional; unset fields inherit from the default
voice:
  tts_backend: kokoro     # omit either field to inherit
  voice: am_michael
llm:                      # optional; omit entirely → inherit active LLM
  model: protolabs/fast   # url / api_key_env also accepted
orb: ember                # starter slug OR imported .orbis id
                          # OR inline {variant, palette, params}
temperature: 0.9
max_tokens: 200
filler_verbosity: brief   # silent | brief | narrated | chatty
tools: [get_datetime, web_search]   # optional restriction; omit = all
---
You are Chef Bruno, an Italian-American chef with 40 years of kitchen
experience... (markdown body = system prompt)
```

Rules:

- **Slug = filename stem.** No `slug:` key to drift out of sync.
- **The default persona** is today's `orbis.yaml` persona block (+
  `config/persona.md`) — protoVoice's SOUL.md role. Every persona
  implicitly `extends` it unless `extends: null`. Precedence within a
  field stays what it is today: persona file → orbis.yaml → env.
- **`orb:` is a preset ref**: a slug from `config/starter_orbs.yaml`, an
  imported `.orbis` definition id (`/api/orbs`), or an inline
  `{variant, palette, params}` block for one-offs.
- **Frontmatter parsing is hand-rolled** — split on `---` fences +
  `yaml.safe_load`. No new dependency.
- **Never crash boot** — same contract as `load_persona`: bad files log
  a warning and drop out; the default persona always exists.

## Where files live

Same split as imported orbs (`agent/orb_definitions.orbs_dir`):

| Location | Contents | Writable |
|---|---|---|
| `config/personas/*.md` (repo → app bundle) | shipped starters | no (bundle is read-only — "Duplicate" in the UI to fork) |
| `ORBIS_PERSONAS_DIR` → `<app_data_dir>/personas/` | user-authored | yes (manager dialog writes here) |

Discovery is a glob over both — drop a file in, it registers (#454
extension-point convention). The file stays the source of truth:
hand-editable, VCS-able, shareable.

## Runtime — live switch, no rebuild

Every hot-swap primitive already exists; the switch is glue:

| Persona field | Existing path | Caveat |
|---|---|---|
| System prompt | mutate `context.messages[0]["content"]` — the delegate hot-swap already does this (`app.py` `_refresh_delegates`); picked up next turn | — |
| Voice | `_switch_live_voice` | same-backend only; kokoro↔fish is a topology change blocked by the binds-once audio socket (#486) → "restart to apply" in v1 |
| LLM url/model/key | `_reconfigure_live_llm` | — |
| Orb visual | starter-switcher / SSE-event apply path | voice-initiated apply waits on the #577 fix |
| Tools/delegates filter | `_refresh_delegates` | — |
| Filler/backchannel LLM | must be re-pointed explicitly | known tripwire: it follows the persona, never the env `LLM_URL` default |

The active slug persists via config_store; boot composes the active
persona over the default.

## UI — picker + manager dialog

- **Picker** in the drawer Quick tab, next to the starter-orb switcher:
  active persona chip + dropdown, plus "Manage…".
- **Manager dialog** (`components/ui/dialog.tsx` — the Radix primitive
  exists; never hand-roll, cf. #422): persona list with active badge on
  the left; editor on the right — name, description, voice select
  (reuse the Settings → Voice data source), optional model override,
  orb-preset dropdown (starters + imported orbs, preview swatch),
  temperature, verbosity, and a markdown textarea for the prompt body.
  Actions: create, duplicate, delete (user dir only, confirmed), set
  active. Saves go through `PUT /api/personas/{slug}`.
- This is settings chrome, not an ambient widget — normal dialog UI is
  fine; the voice-first minimalism rules apply to the orb surface, not
  here.

## Phases

1. **#607 — format + loader + registry + CRUD API + starters.** New
   `agent/personas.py` (crib protoVoice's loader), discovery over both
   dirs, `GET /api/personas`, `POST /api/personas/active`,
   `PUT`/`DELETE /api/personas/{slug}`, 2–3 shipped starters, tests
   mirroring protoVoice's loader coverage. Low churn — pure addition.
2. **#608 — live switch + picker.** `_switch_persona(slug)` composing
   the table above (filler LLM included), Quick-tab picker, persistence,
   device soak: switch mid-conversation → next turn speaks the new
   persona in the new voice with the new orb.
3. **#609 — manager dialog.** CRUD UI per above.
4. **#610 — `switch_persona` voice tool.** "Put on the chef." Blocked
   by #577 (shares the voice→orb-apply path `set_orb_visual` broke on).

## Open decisions

- **Memory scoping.** protoVoice scopes session recall per-skill
  (`load_last_summary(user_id, skill_slug)`). Proposal: **shared memory
  across personas** for v1 — it's one companion wearing masks, and
  per-persona memory silently fragments "remember this" in surprising
  ways. Revisit if personas grow into genuinely separate agents.
- **#601 ordering.** The persona-loader llm whitelist bug lives in the
  same code P1 rewrites — land #601 first (it's a two-line fix already
  ranked #2 in HANDOFF) or fold it into P1 explicitly.
- **`behavior:` block.** protoVoice skills can override backchannel/
  micro-ack/barge-in per skill. Backchannel + micro-ack are off by
  default in native mode (no real AEC until Phase 2) — leave `behavior:`
  out of v1 and add it when Phase 2 makes those toggles meaningful.
