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

Status: **free public beta for Apple Silicon Mac.** Download the signed,
notarized app at [orbis.protolabs.studio/download](https://orbis.protolabs.studio/download).
See [getting started](./docs/tutorials/getting-started.md) for installation and
your first conversation, or [DECISIONS.md](./DECISIONS.md) for the architecture.

## What ORBIS is

- **Voice-first.** Real-time bidirectional audio through the native
  macOS Tauri shell, Rust-owned microphone/speaker transport, and the
  Pipecat pipeline.
  Text fallback is possible but the pitch is "talk to it, don't chat."
- **Router-first.** The orb's primary capability is delegating to
  your configured agents — it's the voice frontend for the AI stack
  you already have, not another agent framework.
- **Companion-layer.** Persistent conversational recall and persona settings,
  with a customizable orb. Personality and mood machinery is available;
  the emotional layer is disabled by default.
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

Requirements: Apple Silicon Mac, Python **3.11** (matching the shipped sidecar),
Bun, Rust for the native shell, and a language model. See [BUILDING.md](./BUILDING.md)
for the complete native build setup. The wizard offers **Built-in (MLX)**,
which runs Qwen3.5-4B in-process after a roughly 2.5 GB model download; it
currently opens on the **Ollama** preset. You can also choose LM Studio, vLLM,
a hosted provider, or another OpenAI-compatible endpoint.

The native Mac build uses Parakeet speech recognition and Kokoro speech
synthesis on-device. Python-only development defaults to Whisper unless
configured otherwise. Cloud speech is optional. First-run runtime and speech
downloads need internet access and about 3 GB of disk, with additional space
for a local language model. Fully local inference can run offline afterward.
The Docker development path supports NVIDIA GPU or CPU hosts; see below.

```bash
# One-time
uv sync --python 3.11 --extra test --extra lint
cp .env.example .env       # optional — fallback env vars and runtime tuning
# config/orbis.yaml is auto-written by the first-run setup wizard

# Fast backend/UI iteration
cd web && bun install && bun run dev   # frontend on :5173
# in a second shell:
.venv/bin/python app.py                # backend on :7866
```

For the native shell and packaged sidecar flow, use the desktop docs:

```bash
scripts/preflight-native-audio-host.sh
scripts/nuke-and-rebuild.sh --launch --tail
```

On first boot, the **setup wizard** walks through microphone permission,
speech-model downloads, names, a language model, and a starter orb. The LLM
step requires a successful connection check. After setup, wait for voice
readiness and double-click the orb to talk.

The bundled `hub` delegate targets protoAgent at `127.0.0.1:7870`.
protoAgent is a separately managed service: start it before delegating work,
or configure an agent you already run. ORBIS can hold a conversation without
the hub, using its own chosen language model.

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

Model inference is slower on CPU. Fish TTS is opt-in via the `fish`
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
│  personality / mood          │
└──────────────────────────────┘
```

## Tool surface

ORBIS's voice agent has a deliberately small set of tools:

- **`delegate_to(target, query)`** — hand off to one of your
  configured agents. A2A-compatible or OpenAI-compatible. Results
  stream back through the delivery controller and narrate naturally.
- **Reminders and inbox** — schedule, list, or cancel reminders and check
  pending messages from your agents.
- **Persona switching and personality adjustment** — select a configured
  persona by voice or ask for a personality adjustment.

Orb visual control is handled outside the agent's tool surface.

Deeper tool use belongs to your delegates. The older in-process orchestration
path remains during migration; the durable direction is the protoAgent hub.

## Memory

SQLite stores conversational state locally in the app's persistent data
directory (override with `ORBIS_DB_PATH`). Tables include:

- `sessions` — one row per voice session (with FTS5 search)
- `facts` — structured storage retained during migration. Long-term knowledge
  belongs to the protoAgent hub; conversational recall uses session summaries.
- `personality_axes` — 10 slow-drift axes (playful↔serious,
  warm↔guarded, sarcastic↔sincere, verbose↔terse, hopeful↔cynical,
  grandiose↔grounded, probing↔incurious, philosophical↔pragmatic,
  independent↔clingy, curious↔bored)
- `personality_events` — append-only drift log
- `mood` — short-term (valence / arousal / guardedness)

No graph DB. No Neo4j. No vector DB. The "poor-man's Graphiti on
SQLite" shape — see [DECISIONS.md § Memory](./DECISIONS.md#memory).

## Configuration

- `config/orbis.yaml` — persona (slug, name, system prompt, LLM
  knobs, filler verbosity), voice (TTS provider + voice id), orb
  (starter variant / palette / params). Copy from
  `config/orbis.example.yaml`. Saved config is authoritative; environment
  variables such as `SYSTEM_PROMPT` are fallbacks for omitted fields.
  Re-read via `POST /api/persona/reload`
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

[Apache License 2.0](./LICENSE) — © 2026 protolabs.studio. See [NOTICE](./NOTICE).
Contributions are accepted under the same license (Apache-2.0 §5), so there's no
separate CLA. The app and orb customization are free; the paid entitlement
system has been removed.
