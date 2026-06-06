# ORBIS

<p align="center">
  <img src="https://i.postimg.cc/Kjnzqnzm/orbis.png" alt="ORBIS — voice-first AI companion" width="720"/>
</p>

> Voice-first AI companion. An orb that talks to you, remembers you,
> and routes the heavy lifting to your existing agents.

ORBIS is a single-owner native desktop app. You talk to the orb; it
talks back in real time; it remembers you across sessions; it hands
off complex tasks to whatever agents you've configured (A2A fleet
agents, OpenAI-compatible endpoints). The differentiator is the
*companion* layer — persistent memory, slow personality drift, moods,
and a visible expressive form — around a thin voice-routing agent.

Status: **active development.** Architecture is locked; feature work
is underway. See [DECISIONS.md](./DECISIONS.md) for the frozen
architectural snapshot.

## What ORBIS is

- **Voice-first.** Real-time bidirectional audio through the native
  macOS Tauri shell, Rust-owned microphone/speaker transport, and the
  Pipecat pipeline.
  Text fallback is possible but the pitch is "talk to it, don't chat."
- **Router-first.** The orb's primary capability is delegating to
  your configured agents — it's the voice frontend for the AI stack
  you already have, not another agent framework.
- **Companion-layer.** Persistent memory (SQLite-backed), slow-drift
  personality axes, short-term mood state, soft-neglect behavior over
  days-of-silence, visible personality panel for the user to peek at.
- **Single-owner.** One instance, one owner. Multi-device access comes
  through native desktop ports after the Mac release path stabilizes.
  Not multi-tenant.

## What ORBIS isn't

- Not another coding agent (OpenCode / Claude Code / Goose / Aider
  are all fine; delegate to them instead).
- Not a game (no progression mechanics, no collectibles as gameplay,
  no social visits).
- Not a replacement for ChatGPT — your reasoning still lives in
  whichever model you've wired up.
- Not a PWA or browser voice app. The supported runtime is native
  Tauri desktop; Linux and Windows desktop support come after the Mac
  native-audio build is stable.
- Not gacha, loot boxes, energy timers, or FOMO-driven monetization.

## Running it (development)

Requirements: Python 3.11+, Bun or npm, and an LLM endpoint. The
desktop app's recommended path is the new **Built-in (MLX)** preset
which runs Qwen3.5-4B (or any `mlx-community/...` model) in-process
via Apple's MLX framework — zero extra install, ~2.5GB first-run
download. Other choices in the wizard: Ollama, LM Studio, vLLM, or
any of the OpenAI/Anthropic/Groq/DeepSeek/OpenRouter/Together/
Mistral/Fireworks/Moonshot/xAI cloud providers. Everything —
including Whisper STT + Kokoro TTS — runs on CPU by default, so no
GPU is required outside of the LLM. A CUDA GPU is strongly
recommended for the *non-Mac* dev path; Apple Silicon Macs use the
unified-memory GPU automatically via MLX + Metal-accelerated Whisper.
See [Docker — with / without GPU](#docker--with--without-gpu) below.

```bash
# One-time
cp .env.example .env       # optional — env vars for pro setups
# config/orbis.yaml is auto-written by the first-run setup wizard

# Fast backend/UI iteration
cd web && bun install && bun run dev   # frontend on :5173
# in a second shell:
python app.py                          # backend on :7866
```

For the native shell and packaged sidecar flow, use the desktop docs:

```bash
scripts/preflight-native-audio-host.sh
scripts/nuke-and-rebuild.sh --launch --tail
```

On first boot, the **setup wizard** walks you through: name yourself +
name the orb, pick an LLM provider (15 presets, with live "test
connection" + model-list fetch + Ollama / LM Studio auto-detect if
they're running), pick a starter orb, grant microphone access, hatch.
Ends in the native app ready to talk.

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

Tailnet-hosted backend access is still possible for development and
automation, but the browser/PWA voice runtime is not supported. The
Drawer → Settings → Access panel accepts the owner API key for API
auth (generate with
`python3 -c "import secrets; print('pv_ak_' + secrets.token_urlsafe(32))"`
and write it into `config/users.yaml`).

## Architecture at a glance

```
┌──────────────────────────────┐
│  macOS Tauri app             │
│  (orb viz + drawer)          │
└────────┬─────────────────────┘
         │ native PCM socket
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

Orb visual control is handled outside the agent's tool surface.

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
- `entitlement_cache` — the stored offline license key, re-verified
  against the build's public key on every gate check

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

## Testing

```bash
.venv/bin/python -m pytest        # full backend suite
cd web && bun run build           # type-check + build frontend
scripts/check-macos-release-config.py
```

## Project docs

📚 **[docs/](./docs/)** is organised by [Diátaxis](https://diataxis.fr)
— tutorials, how-to guides, reference, and explanation. Start there to *use*
or *understand* ORBIS (e.g. [getting started](./docs/tutorials/getting-started.md)).

The docs below are for *picking up the codebase*, read in this order on a cold
pickup:

1. **[STATUS.md](./STATUS.md)** — current snapshot. Where the
   codebase is, module map, what's shipped vs still pending.
   Always up to date.
2. **[DECISIONS.md](./DECISIONS.md)** — frozen architecture
   decisions. Amendments are additive; don't silently change
   direction.
3. **[HANDOFF.md](./HANDOFF.md)** — QA checklist, known open
   questions, and ordered next steps. Written for a teammate (or
   tomorrow-you) picking ORBIS up fresh.
4. **[docs/internal/proactive-companion.md](./docs/internal/proactive-companion.md)** —
   **user-facing guide**: what ORBIS can do as a proactive companion
   (reminders, hand-offs to your agents, external pings), how to drive
   it by voice, and the full config-knob reference.
5. **[docs/internal/orb-visualizer.md](./docs/internal/orb-visualizer.md)** —
   engineering reference for the orb plugin system inherited from
   protoVoice (variant registry, shared signal bus, palette system,
   field types).

Seed provenance: this repo started as a squashed fork of
[protoLabsAI/protoVoice](https://github.com/protoLabsAI/protoVoice)
@ v0.12.1, then was carved to ORBIS scope. See the commit history
from the `initial commit` through the `carve:` series for what came
out of the seed.

## License

TBD.
