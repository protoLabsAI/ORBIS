# ORBIS — engineering handoff

Companion to [DESIGN.md](./DESIGN.md). DESIGN is *what ORBIS is*; this
document is *how to carve the seed into it*. The product vision was
written first on purpose — architecture decisions should serve the
product, not dictate it.

## Seed provenance

This repo was seeded from [protoVoice](https://github.com/protoLabsAI/protoVoice)
at tag `v0.12.1` (2026-04-22). Squashed to a single commit; no upstream
history preserved. If you need to understand why a piece of seed code
exists, `git log` in protoVoice has the reasoning.

The seed is a *voice-agent product*, not an ORBIS starting point. You
inherit it because it has the single most valuable component already
built: the orb shader and plugin system in `web/src/plugins/orb/`.
Everything else in the seed is scaffolding you'll delete.

## What to keep

| Path | Why | Effort to adapt |
|:---|:---|:---|
| `web/` (most of it) | React 19 + Vite 6 + shadcn/ui + Tailwind 4 + PWA + plugin slot registry. Exactly the frontend stack ORBIS needs. | Low — strip voice-agent-specific plugins |
| `web/src/plugins/orb/` | The orb itself: R3F renderer, variant registry (Fractal / Nebula / Crystal / Particles), shared driver hooks, broadcast bus, localStorage preset store. The crown jewel. Documented at `docs/reference/orb-visualizer.md`. | None — ship as-is, extend with ORBIS variants |
| `web/src/plugins/orb-settings/` | Field/panel machinery for editing orb parameters. Will morph into the Sanctum/Collection view. | Medium — reshape UI; keep primitives |
| `auth/users.py` | API-key → User dependency; role split (admin/user); Infisical integration. Will upgrade later to real auth (passkeys / OAuth) before Phase 3 (payments) but this pattern is fine through Phase 2. | Low — rename `allowed_skills` → `allowed_variants` or drop the concept |
| `scripts/version.py` + `.github/workflows/` | Release tooling (prepare-release, release, docker-publish, docs). Retarget image name and you're done. | Low — update image name in `release.yml` |
| `tests/` | FastAPI TestClient pattern + pytest setup + `.venv` helpers. Templates for ORBIS tests; delete the specific ones once subjects go. | Use as template |
| `Dockerfile` stage 1 (the bun web build) | Multi-stage build with bun-built web dist copied into Python runtime. Reuse. | Low — update COPY paths after the carve |
| `.dockerignore`, `.gitignore`, `.env.example` | Conventions set. | Update values, keep shape |
| `README.md` + docs conventions | mkdocs-material with guides / reference / explanation / tutorials split. Pattern is good even if content all gets rewritten. | High — nearly all content is about voice agents |

## What to delete (hard)

Delete these directories/files entirely. None of their code serves ORBIS.

| Path | What it does in protoVoice | Why you don't need it |
|:---|:---|:---|
| `a2a/` | Agent-to-Agent protocol (JSON-RPC, message/send, message/stream, inbound + outbound, push callbacks) | ORBIS has no fleet-agent use case |
| `voice/` | STT / TTS glue — Whisper, Fish-speech, Kokoro, silero VAD | You're replacing the whole voice stack with OpenAI Realtime API |
| `agent/` | Filler generator, delivery controller, memory pipeline (old shape), trace session, skill-slug user state. Pipecat-specific. | Your memory architecture is totally different (hot/warm/cold — see DESIGN.md § "Memory"); this one doesn't transfer |
| `skills/` | Persona YAML loader with `extends:` inheritance | Interesting as prior art for catalog-with-inheritance but overfit to voice personas. Rewrite fresh for variants. |
| `config/skills/` + `config/SOUL.md` + `config/delegates.yaml` | Persona catalog + delegate registry | Gone |
| `static/` | Legacy vanilla-HTML client, pre-React (already deprecated in protoVoice) | Gone |
| `Dockerfile.fish` + `docker-compose.yml`'s fish service | Fish-speech TTS server container | Gone |
| Most of `docs/` | Pages are overwhelmingly about Pipecat / voice backends / skills / delivery policies / A2A | Keep `docs/reference/orb-visualizer.md` only; start fresh |
| `app.py` | 1200-line FastAPI app for the voice agent | Gut it; rewrite as a ~50-line skeleton |

## What to rewrite

| Path | Why | Approach |
|:---|:---|:---|
| `app.py` | Strip voice-pipeline routes; keep auth dependency + basic endpoints | Start from scratch: one `whoami`, one `orbs list`, one `session start` for Realtime API relay |
| `config/variants/*.yaml` | Catalog of orb variants (new) | Each file: `slug`, `name`, `rarity`, `shader` (variant + palette + params), `voice_id`, `persona_descriptor`, `trait_seeds`, `sanctum_theme` |
| Memory layer | Seed's `agent/session_store.py` is text-summary-based; ORBIS needs structured hot/warm/cold (see DESIGN.md) | New module from scratch; ignore the seed's shape |
| Personality axes + state | Does not exist in seed | New — `personality/axes.py`, `personality/traits.py`, server-side JSON per user × generation |
| Idle Resonance | Does not exist in seed | New — trivial: `accumulated = rate × (now - last_seen)`, capped, server-side |
| Sanctum state + visits | Does not exist in seed | New — tables: `sanctums`, `sigils`, `visitor_sessions` |

## What `pyproject.toml` needs

Current deps (voice-agent heavy):

```
pipecat-ai[webrtc,silero,openai]>=1.0
torch>=2.5
transformers>=4.46
accelerate
kokoro>=0.9
vllm>=0.18
soundfile
soxr
numpy
langfuse>=3.0
```

ORBIS doesn't need any of those. Target deps:

```
fastapi
uvicorn[standard]
pyyaml
httpx
pydantic
openai        # for Realtime API session tokens + memory extractor LLM calls
stripe        # Phase 3
python-multipart
```

SQLite via stdlib through Phase 2; add Postgres driver when scale demands.

This cuts the Docker image from ~6 GB to ~200 MB and drops cold-start
time from minutes to seconds. Do this on day 1.

## Suggested Day-1 demolition

One commit per directory for review-ability. Target end-of-day 1 state:
a repo that still builds the web dist but has a stub backend.

```bash
# Day 1
git rm -r a2a voice agent skills static
git rm Dockerfile.fish
git rm config/delegates.yaml config/SOUL.md
git rm -r config/skills
# Strip docs to just the orb reference
find docs -type f ! -name 'orb-visualizer.md' -delete
# Strip app.py (rewrite as minimum skeleton)
# Strip pyproject.toml deps (replace with target list above)
# Update Dockerfile to drop stage-2 GPU/CUDA base, use python:3.12-slim
```

Day 2-5: stand up the Realtime-API relay, the minimum session
endpoint, hatch flow, and first starter variant.

## Design questions engineering needs to resolve before Phase 1

These aren't in DESIGN.md because they're implementation, not product.
Resolve them before committing to a Phase 1 branch.

1. **Storage.** SQLite is the right default (single file, no ops,
   survives container restarts with a mounted volume). When do we
   switch to Postgres? Rough trigger: when we need horizontal scale or
   cross-process writes. Likely Phase 3 or later. Until then, SQLite.
2. **Realtime API session broker.** Clients should *not* hold OpenAI
   credentials. Backend mints short-lived ephemeral session tokens and
   hands them to the browser. This is the pattern OpenAI documents; do
   not skip it.
3. **Memory extraction scheduling.** Post-session extractor runs as a
   background task. Simple approach: enqueue on session-end webhook from
   the client, process in a worker. Don't over-engineer — a FastAPI
   background task queue is fine at 1000 daily users.
4. **Audio-reactive shader pipeline.** protoVoice drives orb state from
   server-side RTVI events over a data channel. ORBIS runs Realtime API
   audio through the browser — simpler and more direct. FFT on the
   outgoing audio buffer, feed magnitudes to shader uniforms. The
   broadcast bus in the orb plugin already has the right shape.
5. **Sanctum visitor-mode safety.** Visitor sessions need a different
   system prompt (visitor mode flag, content safety posture). Design
   this before exposing Sanctum URLs publicly.
6. **Memory partitioning.** Hard isolation by `(user_id, orb_generation)`.
   Extractor must write to the owning user's memory only. Visitor-mode
   sessions read memory but write to a *visitor log* (the orb's report
   back to the owner), never to the memory layer itself.
7. **Chronicle immutability.** Once a Rebirth completes, the outgoing
   orb's Chronicle entry is frozen. No retroactive edits. This is a
   product-level rule but has a schema consequence: Chronicle entries
   are append-only.
8. **Variant versioning.** When we buff a shader variant after release,
   what do existing owners see? Option A: frozen at hatch — slug + params
   are captured in the unlock record. Option B: live — everyone gets the
   buff. Recommend hybrid: params are live by default, but the user can
   "freeze" their orb's current look into a keepsake snapshot. Commit to
   a direction in Phase 2 before the catalog gets big.

## Running the seed as-is

Before demolition you can sanity-check the seed builds:

```bash
# Frontend only (what you actually want to keep):
cd web
bun install
bun run dev
# → http://localhost:5173 — you'll see the orb rendering
```

The backend requires a running vLLM + Fish server — don't bother. You're
going to rip the backend out anyway.

## Contact

The protoVoice repo's commit history (through v0.12.1) documents why
each piece of the seed exists. If a design decision in the seed puzzles
you, `git log -p -- <path>` in the protoVoice repo has the commit
message that explains it. Questions beyond that go back to the
protoVoice team.
