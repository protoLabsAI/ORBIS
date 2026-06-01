# Config (`orbis.yaml`)

`orbis.yaml` is ORBIS's configuration file and the source of truth. Most options
are also reachable from the **Settings** panel — the wizard and Settings write
this file for you — but you can edit it directly for headless setups.

Many sections have their own reference page (linked below); this page is the map.

## Where it lives

ORBIS reads `orbis.yaml` from the app's data directory (set up on first run).
An **annotated example** with every option is in the repo:
[`config/orbis.example.yaml`](https://github.com/protoLabsAI/ORBIS/blob/main/config/orbis.example.yaml).

## Environment overrides

Most settings have an `ORBIS_*` / `LLM_*` / `STT_BACKEND` / `TTS_BACKEND` env-var
equivalent that overrides the file. Where a section block is set in
`orbis.yaml`, it takes precedence over the corresponding env vars; leave it out
to fall back to env.

## Sections

### `persona:`

How the orb presents and behaves.

| Key | Meaning |
| --- | --- |
| `name` | The orb's name. |
| `user_name` | What the orb calls you (empty = it won't use a name). |
| `system_prompt_file` | Persona prompt, relative to the config dir (default `persona.md`). Inline `system_prompt:` overrides it. |
| `temperature` | LLM sampling temperature. |
| `max_tokens` | Max reply length. |
| `filler_verbosity` | How chatty the acknowledgements are: `silent` · `brief` · `narrated` · `chatty`. |
| `behavior.speaker_gate` | Optional speaker-verification gate (voiceprint). Omit for owner-trust mode. |
| `behavior.audio_tags` | Optional emotion side-channel (SenseVoice STT only). |

### `voice:` and `stt:`

How ORBIS speaks and hears. See the [Voice reference](./voice) for backends,
voices, and every key.

### `llm:`

The router/personality model, plus optional failover, two-model routing, and a
micro tier. See the [Agent reference](./agent#the-llm).

Delegates aren't in `orbis.yaml` — they're managed in Settings (and a separate
store); see the [Agent reference → Delegates](./agent#delegates).

### `orb:`

The starter orb, picked at first run.

| Key | Meaning |
| --- | --- |
| `variant` | The orb variant (e.g. `fractal`). |
| `palette` | The palette within that variant (e.g. `Aurora`). |
| `params` | Saved parameter overrides. |

See the [Orb reference](./orb).

## See also

- [Voice](./voice) · [Agent](./agent) · [The orb](./orb)
- The annotated [`config/orbis.example.yaml`](https://github.com/protoLabsAI/ORBIS/blob/main/config/orbis.example.yaml).
