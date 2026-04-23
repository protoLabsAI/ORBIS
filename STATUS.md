# STATUS — current snapshot

*Last updated 2026-04-23. Branch: `main`.*

This file is a point-in-time pickup doc. Always up-to-date; read this
first on any resume before digging into code.

## TL;DR

ORBIS is a voice-first AI companion — an orb that talks back in real
time, remembers you across sessions, and delegates heavy reasoning to
your configured agents. Single-owner, tailnet-hostable, SQLite-backed
memory + personality, pipecat voice pipeline with kokoro default TTS.

Repo is in a **runnable, reviewable** state — 35 commits in, 23/23
planned tasks complete. Backend spine + full frontend (setup wizard,
drawer panels, hatch animation, orb plugins) all shipped. Live boot
+ real WebRTC verification still to do by a human.

## Where we are

### Codebase

- **Repo:** [github.com/protoLabsAI/ORBIS](https://github.com/protoLabsAI/ORBIS)
- **Branch:** `main`
- **Tests:** 104 passing (`pytest`), zero failures
- **Build:** frontend typechecks locally; not yet verified on clean
  bun install in CI (frontend hasn't been wired into the release
  pipeline yet — see [HANDOFF.md](./HANDOFF.md))
- **Dockerfile:** builds stage-1 web via bun, stage-2 Python runtime;
  needs a live verify after the deps trim
- **Release pipeline:** `.github/workflows/` retargeted to
  `protoLabsAI/ORBIS` + `ghcr.io/protolabsai/orbis`; not yet fired
  (no tag cut)

### What's shipped

**Backend spine**
- Single-persona loader (`config/orbis.yaml`) replacing the
  protoVoice skills catalog
- Single-owner API-key auth; tailnet-hosted multi-device by design
- Pipecat voice pipeline untouched from seed; kokoro default TTS
- SQLite memory backend — sessions (FTS5), facts (bi-temporal +
  90-day half-life decay), personality axes, mood, entitlement cache
- Personality rendering into prompt + post-session drift analyzer
  (small-LLM call, silent on failure)
- Soft-neglect mood shifts over days of silence
- Tool surface scoped to `delegate_to` + 5 orb self-modification
  tools + `adjust_personality` for directable drift
- TTS pluggable: kokoro / openai-compat / elevenlabs / fish

**API surface** (`/api/*`, auth-gated except where noted)
- `whoami`, `verbosity`, `persona/reload`, `users/reload`
- `starter_orbs` (unauth — wizard uses before auth is set)
- `config` (GET/POST with typed validation)
- `personality` (mood + axes + drift events + session stats)
- `orb/select_starter` (wizard commits pick)
- `entitlement`, `entitlement/checkout`
- `stripe/webhook` (unauth, signature-verified)
- `offer` (WebRTC signalling)
- `delegates/reload`

**Frontend**
- Setup wizard (welcome → access → pick → done → hatch) as a
  full-screen first-run overlay
- Hatch animation (3.6s scripted CSS reveal)
- Drawer with Voice + Orb tabs; Voice tab has Agent, Profile, Access
  panels
- API-key field with password input + `whoami` verification
- Personality panel surfacing mood, top axes, session stats, recent
  drift
- Mood polling plugin (subscribable via `useMood()`)
- Orb plugin system (Fractal, Nebula, Crystal, Particles variants)
  unchanged from seed

### Config files shipped

- `config/orbis.example.yaml` — persona + voice + orb starter
- `config/starter_orbs.yaml` — the curated 8-orb pool
- `config/users.example.yaml` — owner credential shape
- `config/delegates.yaml` — A2A + OpenAI-compat targets
- `.env.example` — env vars (LLM, TTS, Stripe, tracing, etc.)

## Not yet done

Nothing is formally on the task list — but these are the obvious
follow-ups flagged across commits:

- **State + mood authoring editor** — `moodStore` polls, but no
  variant subscribes yet. Per-variant uniform mappings are still
  to build. Authoring UI (drag a slider, see the orb react, save
  the delta) is the full realization of DECISIONS.md's amendment.
- **`_active_skill()` → `get_active_persona()` rename** — the
  compatibility shim works but the naming is stale in app.py and
  a2a/server.py.
- **Live-boot verification** — the Python imports clean and
  frontend builds locally, but a real WebRTC session against a
  running LLM hasn't been smoke-tested end-to-end yet.
- **Docs rebuild** — `docs/` got purged in the demolition; only
  `docs/orb-visualizer.md` survives. Worth authoring a small guide
  set (setup, persona config, delegate config, state/mood editor)
  once the product stabilizes.
- **CI hookup for the new deps trim** — `pyproject.toml` lost vllm
  + ddgs; worth confirming the Dockerfile build + a pytest run
  cleanly in a fresh container.

See [HANDOFF.md](./HANDOFF.md) for the full QA checklist and
open-design questions.

## Module map

```
agent/                         voice-pipeline quality + agent glue
  persona.py                   single-persona loader from orbis.yaml
  personality.py               prompt rendering + drift analyzer
  neglect.py                   soft-neglect mood shifts
  starter_orbs.py              curated pool loader
  config_store.py              read/write + schema validation
  entitlement.py               Stripe checkout / webhook / refresh
  tools.py                     delegate_to + 5 orb tools + adjust_personality
  delegates.py                 A2A + OpenAI-compat unified dispatch
  filler.py / delivery.py      voice-pipeline natural-filler machinery
  backchannel.py / micro_ack.py / bargein.py
  session_store.py             orphan deliveries + legacy text summaries
  tracing.py                   Langfuse integration
  user_state.py                per-user runtime state

auth/                          single-owner API-key auth
  users.py                     User + UserRegistry + require_user
  context.py                   current_user_id / current_session_id ContextVars
  infisical.py                 optional Infisical secret fetch
  __init__.py

a2a/                           A2A inbound + outbound
  server.py                    /a2a routes + webhook handlers
  client.py                    outbound dispatcher

voice/                         STT + TTS pipecat adapters
  stt.py                       Whisper (local or OpenAI-compat)
  tts/__init__.py              provider dispatch
  tts/kokoro.py                default (CPU)
  tts/openai.py                OpenAI-compat
  tts/elevenlabs.py            native WebSocket
  tts/fish.py                  opt-in sidecar

memory/                        SQLite memory backend (new in ORBIS)
  db.py                        Memory facade + schema + migrations
  sessions.py                  SessionsDAL (FTS5)
  facts.py                     FactsDAL (bi-temporal, half-life decay)
  personality.py               PersonalityDAL (axes + events + mood)
  entitlement.py               EntitlementDAL (cache)

web/src/
  App.tsx                      side-effect imports; top-level PipecatClient
  voice/                       pipecat client + state bridge
  components/Drawer.tsx        Sheet + Voice/Orb tabs
  plugins/
    orb/                       R3F orb + variants + store + broadcast bus
    orb-settings/              params editor (in drawer Orb tab)
    voice-panel/               Agent + Profile + Access panels
    status-pill/               connection status indicator
    setup-wizard/              first-run flow + hatch animation
    mood/                      polling store + useMood() hook
  auth/                        apiKey store + useApiKey hook
  lib/api.ts                   typed /api/* fetch wrappers

config/                        user-editable YAML
tests/                         pytest: 104 cases, all green
docs/                          orb-visualizer.md only (rest were purged)
```

## Quick-start

```bash
cd ~/path/to/ORBIS

# One-time
cp .env.example .env                             # edit LLM_URL, keys
cp config/orbis.example.yaml config/orbis.yaml   # optional
cp config/users.example.yaml config/users.yaml   # tailnet hosting only

# Run dev
cd web && bun install && bun run dev             # :5173
python app.py                                    # :7866

# Tests
.venv/bin/python -m pytest                       # 104 passing
```

## Known tripwires (don't change lightly)

Carried forward from the protoVoice seed — these are hard-won
discoveries, still relevant:

- **Browser mic constraints stay at defaults** (AGC/NS/EC on).
  Disabling them broke server VAD.
- **`append_to_context=False`** on every out-of-band TTSSpeakFrame
  (filler, backchannel, delivery). Without it the LLM riffs on its
  own fillers.
- **`cancel_on_interruption=True`** default for sync tools.
- **Fractal orb rotation + uTime wrap at 2π·N** to avoid float32
  precision drift after ~10 min.
- **FTS5 is required** in the SQLite build — ORBIS refuses to start
  without it.
- **Stripe webhook endpoint is unauth** on purpose — signature
  verification is the auth. Don't wrap it in `require_user`.
- **`_active_skill()` is a compat shim** returning the Persona. The
  name is stale; don't mistake it for a surviving piece of the
  skills system.

## One-line rollback

The repo history doesn't carry tags yet. The initial squashed seed
is commit `25bcc9d` (the very first); checking out that commit puts
you back to the unmodified protoVoice v0.12.1 seed before the
demolition.

```bash
git checkout 25bcc9d    # seed state, pre-carve
```
