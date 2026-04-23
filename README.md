# ORBIS

> Voice-first AI companion. An orb that talks to you, remembers you,
> and routes the heavy lifting to your existing agents.

ORBIS is a single-owner desktop / tailnet-hosted app. You talk to the
orb; it talks back in real time; it remembers you across sessions;
it hands off complex tasks to whatever agents you've configured (A2A
fleet agents, OpenAI-compatible endpoints). The differentiator is the
*companion* layer — persistent memory, slow personality drift, moods,
and a visible expressive form — around a thin voice-routing agent.

Status: **active development.** Architecture is locked; feature work
is underway. See [DECISIONS.md](./DECISIONS.md) for the frozen
architectural snapshot.

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

Requirements: Python 3.11+, Bun or npm, a running LLM endpoint
(local vLLM, LiteLLM gateway, OpenAI, or any OpenAI-compatible
URL). Kokoro TTS runs CPU-only and is the default; no GPU required
for TTS.

```bash
# One-time
cp .env.example .env       # edit LLM_URL + any delegate keys
cp config/orbis.example.yaml config/orbis.yaml       # optional — persona tuning
cp config/users.example.yaml config/users.yaml       # optional — owner API key

# Run (two processes)
cd web && bun install && bun run dev   # frontend on :5173
# in a second shell:
python app.py                          # backend on :7866
```

Or one-shot with Docker Compose (kokoro default, no Fish sidecar):

```bash
docker compose up
```

Tailnet hosting: `sudo tailscale serve --bg --https=8443 http://127.0.0.1:7866`
and point your phone / other devices at the tailnet URL.

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
- **`set_variant(name)`, `apply_palette(name)`, `adjust_param(key,
  value)`, `save_preset(name)`, `recall_preset(name)`** — the orb
  self-modifies its appearance when you ask ("be warmer", "try a
  darker look"). Paid unlock gates non-starter variants.

Nothing else ships. Calculator, search, datetime — all subsumed by
whatever agent you delegate to.

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

No graph DB. No Neo4j. No vector DB. The "poor-man's Graphiti on
SQLite" shape — see [DECISIONS.md § Memory](./DECISIONS.md#memory).

## Configuration

- `config/orbis.yaml` — persona (slug, name, system prompt, LLM
  knobs, filler verbosity), voice (TTS provider + voice id), orb
  (starter variant / palette / params). Copy from
  `config/orbis.example.yaml`. Override `system_prompt` at the env
  level with `SYSTEM_PROMPT`. Re-read via `POST /api/persona/reload`
  or `POST /api/config` (which the drawer UI calls).
- `config/starter_orbs.yaml` — the curated pool the setup wizard
  presents at first boot. Ship 8 by default; edit to taste.
- `config/users.yaml` — owner credential (single entry). Omitted =
  single-user fallback (no auth enforced). Required for tailnet
  hosting.
- `config/delegates.yaml` — A2A / OpenAI-compat endpoints the
  `delegate_to` tool can reach.

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
  `ENTITLEMENT_CACHE_DAYS` (default 14); a daily lifespan task
  re-queries Stripe to extend.

Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and
`STRIPE_PRICE_CUSTOMIZATION` in `.env` to enable. Point the Stripe
dashboard webhook at `POST https://<your-host>/api/stripe/webhook`.

## Testing

```bash
.venv/bin/python -m pytest        # full backend suite (100+ tests)
cd web && bun run build           # type-check + build frontend
```

## Contributing

- [DECISIONS.md](./DECISIONS.md) — architecture snapshot, frozen.
  Amendments are additive (don't silently change direction).
- [docs/orb-visualizer.md](./docs/orb-visualizer.md) — engineering
  reference for the orb plugin system inherited from protoVoice.

Seed provenance: this repo started as a squashed fork of
[protoLabsAI/protoVoice](https://github.com/protoLabsAI/protoVoice)
@ v0.12.1, then was carved to ORBIS scope. See the commit history
from the `initial commit` through the `carve:` series for what came
out of the seed.

## License

TBD.
