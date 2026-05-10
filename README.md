# ORBIS

<p align="center">
  <img src="https://i.postimg.cc/Kjnzqnzm/orbis.png" alt="ORBIS — voice-first AI companion" width="720"/>
</p>

> Voice-first AI companion. An orb that talks to you, remembers you,
> and routes the heavy lifting to your existing agents.

ORBIS is a single-owner WebRTC PWA backed by a Python sidecar
(FastAPI + Pipecat). You talk to the orb; it talks back in real time;
it remembers you across sessions; it hands off complex tasks to
whatever agents you've configured (A2A fleet agents, OpenAI-compatible
endpoints). The differentiator is the *companion* layer — persistent
memory, slow personality drift, moods, and a visible expressive form
— around a thin voice-routing agent.

Status: **active development.** Architecture is locked; feature work
is underway. See [DECISIONS.md](./DECISIONS.md) for the frozen
architectural snapshot — including the 2026-04-29 amendments dropping
the Tauri shell and starting the move toward a thin-sidecar
distribution.

## What ORBIS is

- **Voice-first.** Real-time bidirectional audio via WebRTC + Pipecat.
  Text fallback is possible but the pitch is "talk to it, don't chat."
- **Router-first.** The orb's primary capability is delegating to
  your configured agents — it's the voice frontend for the AI stack
  you already have, not another agent framework.
- **Companion-layer.** Persistent memory (SQLite-backed), slow-drift
  personality axes, short-term mood state, soft-neglect behavior over
  days-of-silence, visible personality panel for the user to peek at.
- **Single-owner.** One instance, one owner. Multi-device access via
  tailnet hosting (phone + laptop hit the same instance). Not
  multi-tenant.

## What ORBIS isn't

- Not another coding agent (OpenCode / Claude Code / Goose / Aider
  are all fine; delegate to them instead).
- Not a game (no progression mechanics, no collectibles as gameplay,
  no social visits).
- Not a replacement for ChatGPT — your reasoning still lives in
  whichever model you've wired up.
- Not gacha, loot boxes, energy timers, or FOMO-driven monetization.

## Running it (development)

Requirements: Python 3.11+, Bun or npm, and an LLM endpoint. The
recommended LLM path on Apple Silicon is the **Built-in (MLX)** preset
which runs Qwen3.5-4B (or any `mlx-community/...` model) in-process
via Apple's MLX framework — zero extra install, ~2.5GB first-run
download. Other choices in the wizard: Ollama, LM Studio, vLLM, the
**protoLabs** gateway preset, or any of the OpenAI / Anthropic / Groq
/ DeepSeek / OpenRouter / Together / Mistral / Fireworks / Moonshot /
xAI cloud providers.

**STT and TTS:**

- **Kokoro TTS** runs locally on CPU and is the default — no GPU needed.
- **Whisper STT is opt-in.** Install with `pip install -e ".[whisper]"`
  if you want in-process transcription. Without that extra, the smart
  default flips `STT_BACKEND` to `openai` and you can point STT at
  any OpenAI-compatible `/v1/audio/transcriptions` endpoint (OpenAI,
  the protoLabs gateway, LocalAI, vLLM-omni, etc.) via the Settings
  drawer or `STT_URL`/`STT_API_KEY`/`STT_MODEL` env vars.
- A CUDA GPU is strongly recommended for the *non-Mac* dev path with
  the `[whisper]` extra; Apple Silicon Macs use the unified-memory GPU
  automatically via MLX + Metal-accelerated Whisper.

See [Docker — with / without GPU](#docker--with--without-gpu) below.

```bash
# One-time
cp .env.example .env       # optional — env vars for pro setups
# config/orbis.yaml is auto-written by the first-run setup wizard
pip install -e .                    # base install (no Whisper)
# pip install -e ".[whisper]"        # opt in to in-process Whisper

# Run (single process — the FastAPI sidecar serves the SPA at /)
python app.py                       # http://127.0.0.1:7866

# Or with hot-reload during frontend dev:
cd web && bun install && bun run dev   # Vite dev server on :5173
python app.py                          # backend on :7866
```

Open `http://127.0.0.1:7866` (or `:5173` during dev) and the **setup
wizard** walks you through: name yourself + name the orb, pick an LLM
provider (16 presets including the protoLabs gateway, with live "test
connection" + model-list fetch + Ollama / LM Studio auto-detect if
they're running), pick a starter orb, hatch. Ends in the main app
ready to double-click-to-talk.

### Docker — with / without GPU

The default `docker-compose.yml` reserves GPU 0 so Whisper STT +
Kokoro TTS run on CUDA. It assumes:

- an NVIDIA GPU visible to the host
- NVIDIA driver ≥ 570 (CUDA 12.8 compatible — the torch wheel baked
  into the image is pinned to `+cu128` to match)
- [`nvidia-container-toolkit`](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  installed (`nvidia-ctk` on `PATH`, `nvidia-container-runtime`
  registered with dockerd)

With that in place:

```bash
docker compose up                       # GPU path (default)
```

On a CPU-only host (laptop, shared box with no NVIDIA card, etc.),
layer the CPU override on top — it drops the GPU reservation + runtime
hint so the container boots without the toolkit:

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up
```

Voice still works; it's just slower on CPU (see the latency numbers
in the requirements note above). Fish TTS is opt-in via the `fish`
profile — unrelated to this GPU switch, see `docker-compose.yml` for
that service.

Tailnet hosting: `sudo tailscale serve --bg --https=8443 http://127.0.0.1:7866`
and point your phone / other devices at the tailnet URL. Owner API
key for tailnet auth lives in `config/users.yaml` — there's no UI to
enter it (single-owner installs don't need one); generate with
`python3 -c "import secrets; print('pv_ak_' + secrets.token_urlsafe(32))"`
and write it into `config/users.yaml`. Browser side, the
`apiKeyStore` LocalStorage key (`orbis.apiKey`) attaches the value as
`X-API-Key` on every `/api/*` call and the WebRTC offer.

### Split deployment — hosted SPA, local sidecar

The default install runs the SPA and the API as one process on
loopback. ORBIS also supports the "hosted UI, local sidecar"
topology: a static SPA on (e.g.) `https://orbis.app` that talks
cross-origin to a sidecar the user runs locally with `orbis` (or
`uvx orbis` / a future `npx orbis` wrapper). UI updates ship
without re-releasing the sidecar; user data and credentials stay on
their machine.

Two env vars on the sidecar enable the split posture:

| Var | Purpose |
| --- | --- |
| `ORBIS_ALLOWED_ORIGINS` | Comma-separated CORS allowlist (e.g. `https://orbis.app,http://localhost:5173`). When **unset**, the sidecar is in same-origin mode and the rest of this section is a no-op. |
| `ORBIS_PAIR_TOKEN` | Optional explicit pairing token. Leave unset to let the sidecar mint one and persist it under `~/.orbis/pair_token` (mode 600). The token is printed at boot — paste it into the SPA's connect screen. |

When `ORBIS_ALLOWED_ORIGINS` is set, an HTTP middleware enforces a
`X-Orbis-Pair: <token>` header on every `/api/*` request. CORS
preflights (`OPTIONS`), `/healthz`, and SPA assets are exempt; A2A
traffic on `/a2a` keeps using its own `A2A_AUTH_TOKEN`. Loopback is
**not** a trust boundary against malicious tabs — the pair token is
what stops a random page in another window from talking to the user's
sidecar.

SPA side, set `VITE_ORBIS_BACKEND=https://your-sidecar-url` at build
time to bake in a default backend URL (the SPA will mount the connect
screen automatically), or leave it unset and let the user paste the
URL into the connect screen at runtime — both states persist to the
`orbis.backendUrl` localStorage key. Same-origin builds leave both
unset and the connect screen never renders.

## Architecture at a glance

```
┌──────────────────────────────┐
│  Browser / PWA               │
│  (orb viz + drawer)          │
└────────┬─────────────────────┘
         │ WebRTC
         ▼
┌──────────────────────────────┐        ┌────────────────────┐
│  Pipecat voice pipeline      │◀──────▶│  Your agents       │
│  (STT → ORBIS LLM → TTS)     │  A2A   │  (A2A / OpenAI)    │
│                              │ OpenAI │                    │
│  ORBIS LLM = small/fast      │        │  protoAgent,       │
│  router + personality layer  │        │  Claude Code,      │
│                              │        │  MCP servers,      │
│                              │        │  whatever          │
└─────┬────────────────────────┘        └────────────────────┘
      │
      ▼
┌──────────────────────────────┐
│  SQLite memory backend       │
│  sessions / facts /          │
│  personality / mood /        │
│  entitlement cache           │
└──────────────────────────────┘
```

## Tool surface

ORBIS's voice agent has a deliberately small set of tools:

- **`delegate_to(target, query)`** — hand off to one of your
  configured agents. A2A-compatible or OpenAI-compatible. Results
  stream back through the delivery controller and narrate naturally.
- **`adjust_personality(axis, delta)`** — shift a personality axis
  when you explicitly ask ("be more playful", "be less sarcastic").
- **`check_inbox(priority_floor?)`** — pull messages pushed in by
  external systems (webhooks, cron, sister agents) via
  `POST /api/inbox`. `now`-priority items auto-surface at the next
  session start; the agent calls this tool when you ask "anything
  new?". See [docs/agent-inbox.md](./docs/agent-inbox.md).

Orb visual control is handled outside the agent's tool surface.

Calculator, search, datetime, fetch_url — all subsumed by whatever
agent you delegate to.

## Memory

SQLite single-file embedded store at `data/orbis.sqlite` (override
with `ORBIS_DB_PATH`). Tables:

- `sessions` — one row per voice session (with FTS5 search)
- `facts` — structured `(subject, relation, object)` with bi-temporal
  validity + confidence. 90-day half-life decay curator runs weekly.
- `personality_axes` — 10 slow-drift axes (playful↔serious,
  warm↔guarded, sarcastic↔sincere, verbose↔terse, hopeful↔cynical,
  grandiose↔grounded, probing↔incurious, philosophical↔pragmatic,
  independent↔clingy, curious↔bored)
- `personality_events` — append-only drift log
- `mood` — short-term (valence / arousal / guardedness)
- `entitlement_cache` — local mirror of Stripe verification
- `inbox` — messages pushed in by external systems for the agent
  to pull on demand (priorities `now`/`next`/`later`)

No graph DB. No Neo4j. No vector DB. The "poor-man's Graphiti on
SQLite" shape — see [DECISIONS.md § Memory](./DECISIONS.md#memory).

## Configuration

- `config/orbis.yaml` — persona (slug, name, system prompt, LLM
  knobs, filler verbosity), voice (TTS backend + voice id + optional
  OpenAI-compat URL/Model/API key for custom gateways), stt (backend
  + Whisper model + URL/Model/API key), llm (URL/Model/API key for
  the router brain), orb (starter variant / palette / params). Copy
  from `config/orbis.example.yaml`. Override `system_prompt` at the
  env level with `SYSTEM_PROMPT`. Re-read via `POST /api/persona/reload`
  or `POST /api/config` (which the drawer UI calls).
- `config/persona.md` — voice-first system prompt. Loaded when
  `persona.system_prompt_file: persona.md` is set in the YAML. Edit
  this file to retune the orb's voice without touching code.
- `config/starter_orbs.yaml` — the curated pool the setup wizard
  presents at first boot. Ship 8 by default; edit to taste.
- `config/users.yaml` — owner credential (single entry). Omitted =
  single-user fallback (no auth enforced). Required for tailnet
  hosting.
- `config/delegates.yaml` — A2A / OpenAI-compat endpoints the
  `delegate_to` tool can reach.

The Settings drawer mirrors the YAML for the per-section fields and
writes via `POST /api/config`. Sections: **Mic** (input device), **STT**
(backend + Whisper model + OpenAI-compat URL/Model/API key), **LLM**
(provider URL/Model/API key — Test + Fetch list), **Voice** (TTS
backend + voice picker with download for Kokoro voices + OpenAI-compat
URL/Model/API key), **Agent** (filler verbosity), **Personality**
(mood + axes display), **Developer** (devMode toggle). Toggling
devMode reveals a fourth tab with feature flags + a collapsible event
log tailing RTVI, fetch, and WebRTC events live.

## Paid unlock (optional)

The orb's full customization editor (change variant, palette,
shader params, save presets) is behind a one-time Stripe payment.
Without Stripe env vars set, customization is open by default
(dev mode). With Stripe configured:

- `POST /api/entitlement/checkout` — creates a Stripe Checkout
  Session, returns the URL.
- `POST /api/stripe/webhook` — verifies the signature and grants
  the entitlement on `checkout.session.completed` / revokes on
  `charge.refunded`.
- Local SQLite cache tolerates offline periods up to
  `ENTITLEMENT_CACHE_DAYS` (default 7); a daily lifespan task
  re-queries Stripe to extend.

Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and
`STRIPE_PRICE_CUSTOMIZATION` in `.env` to enable. Point the Stripe
dashboard webhook at `POST https://<your-host>/api/stripe/webhook`.

## Testing

```bash
.venv/bin/python -m pytest        # full backend suite (470+ tests)
cd web && bun run build           # type-check + build frontend
```

## Project docs

All in-repo; read in this order on a cold pickup:

1. **[STATUS.md](./STATUS.md)** — current snapshot. Where the
   codebase is, module map, what's shipped vs still pending.
   Always up to date.
2. **[DECISIONS.md](./DECISIONS.md)** — frozen architecture
   decisions. Amendments are additive; don't silently change
   direction.
3. **[HANDOFF.md](./HANDOFF.md)** — QA checklist, known open
   questions, and ordered next steps. Written for a teammate (or
   tomorrow-you) picking ORBIS up fresh.
4. **[docs/orb-visualizer.md](./docs/orb-visualizer.md)** —
   engineering reference for the orb plugin system inherited from
   protoVoice (variant registry, shared signal bus, palette system,
   field types).
5. **[docs/agent-inbox.md](./docs/agent-inbox.md)** — external
   ingress for messages the agent pulls (webhooks, cron, sister
   agents). Priority model + auth + tool integration.

Seed provenance: this repo started as a squashed fork of
[protoLabsAI/protoVoice](https://github.com/protoLabsAI/protoVoice)
@ v0.12.1, then was carved to ORBIS scope. See the commit history
from the `initial commit` through the `carve:` series for what came
out of the seed.

## License

TBD.
