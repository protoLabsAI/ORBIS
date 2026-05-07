# STATUS — current snapshot

*Last updated 2026-04-29. Branch: `clean-2e8d4ac` (the new way forward
after the Tauri rip; see DECISIONS.md amendments 2026-04-29).*

This file is a point-in-time pickup doc. Always up-to-date; read this
first on any resume before digging into code.

## TL;DR

ORBIS is a voice-first AI companion — an orb that talks back in real
time, remembers you across sessions, and delegates heavy reasoning to
your configured agents. Single-owner, tailnet-hostable, SQLite-backed
memory + personality, pipecat voice pipeline with kokoro default TTS.

**Distribution model:** WebRTC PWA backed by a Python sidecar. The
FastAPI sidecar serves both the React/Vite SPA at `/` and the
`/api/*` routes; one process, no Tauri shell. Planned `pipx install
orbis` / `uv tool install orbis` ships the wheel with `web/dist/`
baked in.

**Voice loop functional end-to-end on Apple Silicon.** Mic permission
flows through the browser; audio over WebRTC reaches Pipecat; Whisper
transcribes (~250ms, optional via `[whisper]` extra); MLX-LM in-process
generates the reply (~350ms TTFB, 42 tok/s decode on M1 Pro 32GB);
Kokoro speaks back (0.16× realtime). First-audio-out per turn
~1.0-1.2s on M1 base; scales ~2× per Apple-Silicon generation.

Repo is **runnable + tested live** — setup wizard ships end-to-end,
voice session connects + responds with the full STT → LLM → TTS chain
producing audio. 470+ passing unit tests.

## Where we are

### Codebase

- **Repo:** [github.com/protoLabsAI/ORBIS](https://github.com/protoLabsAI/ORBIS)
- **Branch:** `clean-2e8d4ac` (post-Tauri-rip working branch).
  Old `main` preserved as diverged-and-stale.
- **Tests:** 470+ passing (`pytest`), zero failures
- **Build:** frontend bundles cleanly via `bun run build`; Python
  sidecar serves the dist at `/` plus `/api/*` JSON routes
- **Live verified:** setup wizard + hatch animation + voice session
  round-trip confirmed working in browser against the running sidecar
- **Release pipeline:** desktop-build CI deleted alongside the Tauri
  shell. Next release path is a PyPI / GH-release wheel with
  `web/dist/` baked in (T49 — cut 0.2.0)

### What's shipped

**Backend spine**
- Single-persona loader (`config/orbis.yaml`) with `persona`, `voice`,
  `llm`, and `orb` blocks. Env overrides per-field; persona wins when
  explicitly set
- Single-owner API-key auth; tailnet-hosted multi-device by design
- Pipecat voice pipeline; kokoro default TTS
- SQLite memory — sessions (FTS5), facts (bi-temporal + 90-day
  half-life decay), personality axes, mood, entitlement cache
- Personality rendering into prompt + post-session drift analyzer
- Soft-neglect mood shifts over days of silence
- Tool surface: `delegate_to` + 5 orb-control tools + `adjust_personality`
- TTS pluggable: kokoro / openai-compat / elevenlabs / fish
- **LLM factory** (`voice/llm/`) — pluggable adapters: OpenAI-compat,
  Ollama-native (uses /api/chat so `think:false` actually works),
  MLX-LM in-process for Apple Silicon. Provider auto-detected from
  URL shape; explicit override via persona config. See
  `DECISIONS.md` § "LLM factory + MLX-LM as Apple-Silicon default".
- LLM-endpoint probing (test, model list, local auto-detect, MLX
  HF-id validation) — see below

**Distribution (post-Tauri-rip)**
- **WebRTC PWA + FastAPI sidecar.** The Python app serves both the
  built `web/dist/` (vite-plugin-pwa) at `/` and the `/api/*` REST
  surface — one process, no native shell. The browser's media-capture
  flow handles mic permission through the platform; we don't ship
  Obj-C shims.
- **Planned shipping path:** `pipx install orbis` / `uv tool install
  orbis` (T49 — cut 0.2.0). Wheel includes `web/dist/` via
  `[tool.hatch.build.targets.wheel.force-include]` so a fresh install
  comes with the SPA baked in.
- **Tauri shell deleted in commit 9b52d97.** See DECISIONS.md
  amendment 2026-04-29.

**Voice loop benchmarks** — Apple M1 Pro 10-core 32GB (MacBookPro18,1),
macOS 26.2, current defaults, 10-turn run:
- STT (Whisper-base.en): 244ms p50 for 3s clip
- LLM TTFB (MLX Qwen3.5-4B 4-bit): 327ms p50, 422ms p95
- LLM decode: 45 tok/s steady-state
- TTS (Kokoro): 294ms TTFA p50, 0.13× RTF (~7.7× faster than realtime)
- End-to-end first-audio-out: ~1.0s per turn
- Repeatable: `python scripts/bench.py --turns 10` (script prints
  the hardware fingerprint at the top of every run for context)

**API surface** (`/api/*`, auth-gated except where noted)
| Route | Method | Auth | Purpose |
|:---|:---:|:---:|:---|
| `whoami` | GET | ✓ | Resolve owner identity |
| `verbosity` | GET/POST | ✓ | Filler verbosity for session |
| `starter_orbs` | GET | — | Curated pool (wizard) |
| `config` | GET/POST | ✓ | Read + patch `config/orbis.yaml`. POST rejects `orb` block when caller lacks the customization entitlement |
| `personality` | GET | ✓ | Mood + axes + drift events + session stats |
| `orb/select_starter` | POST | ✓ | Wizard's starter pick |
| `persona/reload` | POST | ✓ | Re-read `config/orbis.yaml` |
| `delegates/reload` | POST | ✓ | Re-read `config/delegates.yaml` |
| `users/reload` | POST | ✓ | Re-read owner credential |
| `llm/test` | POST | — | Real chat.completions round-trip + latency |
| `llm/models` | POST | — | `GET /models` with Ollama fallback |
| `llm/detect_local` | GET | — | Parallel probe Ollama + LM Studio |
| `tts/voices` | GET | — | List voices for a backend (kokoro static, fish via list_references, openai canonical 6, elevenlabs free-text) |
| `tts/voices/download` | POST | — | Eagerly download a Kokoro voice tensor into the HF cache (idempotent) |
| `entitlement` | GET | ✓ | Paid-tier state |
| `entitlement/checkout` | POST | ✓ | Stripe Checkout session |
| `stripe/webhook` | POST | sig | Grant/revoke entitlement |
| `offer` | POST/PATCH | ✓ | WebRTC signalling |
| `metrics` | GET | ✓ | Counters |
| `healthz` | GET | — | Process shape |

**Frontend (React + Vite + shadcn)**
- First-run setup wizard (welcome → names → llm → pick → done → hatch)
  - Names: `persona.user_name` + `persona.name` collection
  - LLM: 16 provider presets — featured: Built-in (MLX) / Ollama /
    **protoLabs** (api.proto-labs.ai/v1) / OpenAI / Anthropic /
    Custom; plus a "Show all" expander for the long tail (LM Studio,
    vLLM, Groq, DeepSeek, OpenRouter, Together, Mistral, Fireworks,
    Moonshot, xAI, LiteLLM). Live model-list fetch + "Test connection"
    real round-trip + local-detect banner (emerald callout when Ollama
    or LM Studio is running on localhost).
  - Pick: 8 starter orbs, each with a palette-derived gradient swatch
    and a fullscreen preview modal (live shader, drag-to-rotate)
  - Hatch: 3.6s CSS reveal (seed → flare → fade)
- Drawer (post-2026-04-29 reshuffle): **Orb / Settings** tabs by
  default, **+ Dev** when `orbis.devMode` is on.
  - **Orb tab:** variant / palette / param editing (gated by
    entitlement)
  - **Settings tab** (single drawer for everything that's not orb-viz):
    Mic (input device) → STT (backend + Whisper-model + URL/Model/
    API-key for openai-compat) → LLM (URL/Model/API-key + Test +
    Fetch list) → Voice (TTS backend + voice picker with Kokoro
    download button + URL/Model/API-key for openai-compat) → Agent
    (verbosity) → Personality (mood + axes display) → Developer
    (devMode toggle).
  - **Dev tab** (when devMode on): feature-flag panel scaffold +
    collapsible Event log tailing RTVI / WebRTC / fetch events live.
- Mood polling plugin — subscribable via `useMood()`
- Orb plugin system (Fractal / Nebula / Crystal / Particles)

### Config files shipped

- `config/orbis.example.yaml` — persona + voice + llm + orb
  (new llm block documents OpenAI / LiteLLM-gateway / local vLLM examples)
- `config/starter_orbs.yaml` — curated 8-orb pool
- `config/users.example.yaml` — owner credential shape
- `config/delegates.yaml` — A2A + OpenAI-compat targets
- `.env.example` — env vars (LLM, TTS, Stripe, tracing, etc.)

## Recent cleanup track (2026-04-27 → 2026-04-29)

Two-day refactor sprint after pulling out of the Tauri infra fight.
Changes on `clean-2e8d4ac`:

```
6569aad feat(deps): Phase A — strip Whisper from default install
e79e690 chore(ui): merge Logs into Dev tab as collapsible
efedf05 feat(ui): lift STT env vars (backend/whisper-model/url/...) into settings panel
bc1c57c feat(ui): lift OpenAI TTS URL/Model/API key from env into the UI
ee11909 feat(ui): free-type voice for openai-compatible backend
2c44b27 feat(ui): dev mode toggle + Dev/Logs drawer tabs
d94fa75 feat(ui): TTS panel polish + move to voice drawer
8d70275 feat(ui): TTS voice picker driven by /api/tts/voices
d302d9e chore(ui): drop Owner API key field from settings drawer
53e9b78 chore(ui): relocate Owner API key + protoLabs preset
5e9bb5a fix(voice): MicroAckInjector pipeline placement, tts_backend
                    case, voice-first persona
9b52d97 chore: remove Tauri/PyApp desktop shell
```

Theme: settings UI is the source of truth, env vars are escape
hatches. Whisper opt-in via `[whisper]` extra. Tauri gone. See
DECISIONS.md amendments 2026-04-29 for the architectural commitments.

## Pending follow-ups (not blocking)

- **Phase B — Web Speech client-side STT** (T61). Browser does the
  STT, server receives transcripts via custom RTVI message; whisper
  becomes truly opt-in for offline / privacy-sensitive deployments.
- **Phase C — kokoro extra + speechSynthesis** (T62). Same treatment
  for TTS half once STT path is settled. Eventually torch-free
  default install.
- **Deepgram + AssemblyAI / Cartesia / Soniox streaming STT** (T63).
  Server-driven streaming alternative to Whisper segmented.
- **Cut 0.2.0 release** (T49). PyPI / GH-release wheel; pipx install
  → orbis serve.
- **Per-variant mood visual mapping** — `moodStore` polls, but no
  variant subscribes yet. Each orb shader needs its own mood → uniform
  translation.
- **State + mood authoring editor** — drag-a-slider-see-the-orb-react
  surface for users to author their own state/mood mappings. Paid-tier
  feature per DECISIONS.md amendment.
- **`_active_skill()` naming rename** — compat shim returning Persona;
  cosmetic but worth a rename pass.
- **ACP / MCP / CLI subprocess delegates** — scoped out; users who
  want CLI-tool delegation wrap their CLI in A2A themselves.
- **Frontend CI** — no `bun run build` in the release pipeline yet.

See [HANDOFF.md](./HANDOFF.md) for the full QA checklist + open design
questions + ordered next steps.

## Module map

```
agent/                         voice-pipeline + agent glue
  persona.py                   single-persona loader from orbis.yaml
  personality.py               prompt rendering + drift analyzer
  neglect.py                   soft-neglect mood shifts
  starter_orbs.py              curated pool loader
  config_store.py              read/write + schema validation
  entitlement.py               Stripe checkout / webhook / refresh
  llm_probe.py                 ping + list_models + detect_local
  tools.py                     delegate_to + 5 orb tools + adjust_personality
  delegates.py                 A2A + OpenAI-compat unified dispatch
  filler.py / delivery.py      voice-pipeline natural-filler machinery
  backchannel.py / micro_ack.py / bargein.py / echo_guard.py / prosody.py
  session_store.py             orphan deliveries + legacy text summaries
  tracing.py                   Langfuse integration
  user_state.py                per-user runtime state

auth/                          single-owner API-key auth
  users.py                     User + UserRegistry + require_user
  context.py                   ContextVars for user/session tracking
  infisical.py                 optional Infisical secret fetch

a2a/                           A2A inbound + outbound
memory/                        SQLite memory backend (sessions/facts/personality/mood/entitlement)
voice/                         STT + TTS pipecat adapters (kokoro/openai/elevenlabs/fish)

web/src/
  App.tsx                      side-effect imports; top-level PipecatClient + LogsCollector
  voice/                       pipecat client + state bridge
  shared/
    devMode.ts                 localStorage-backed devMode store + useDevMode hook
    logBus.ts                  in-memory ring buffer + useLogBus hook
  components/Drawer.tsx        Sheet + Orb/Settings/Dev tabs (Dev gated by devMode)
  plugins/
    orb/                       R3F orb + variants + store + broadcast bus
    orb-settings/              params editor (drawer Orb tab)
    settings-panel/            single Settings drawer — Mic, STT, LLM, Voice
                                (TTS), Agent (verbosity), Personality, Developer
                                — see SettingsPanel.tsx
    dev-panel/                 Dev tab — feature flags + collapsible event log
    logs-panel/                LogsCollector (App-mounted) + LogsPanel
                                (rendered inside DevPanel's collapsible)
    status-pill/               connection status indicator
    setup-wizard/              first-run flow + hatch animation
    mood/                      polling store + useMood() hook
  auth/                        apiKey store + useApiKey hook
  lib/api.ts                   typed /api/* fetch wrappers

config/                        user-editable YAML
  persona.md                   voice-first system prompt (referenced
                               via persona.system_prompt_file)
tests/                         pytest: 470+ cases
  test_users.py                auth primitive
  test_memory.py               SQLite DALs
  test_persona.py              persona loader
  test_personality_render.py   prompt rendering math
  test_neglect.py              soft-neglect day-buckets
  test_starter_orbs.py         pool loader
  test_config_store.py         schema + read/write/merge
  test_config_endpoint_gate.py /api/config paid-tier gate
  test_entitlement.py          Stripe glue
  test_llm_probe.py            ping/models/detect_local (respx-mocked)
docs/                          orb-visualizer.md only
```

## Quick-start

```bash
cd ~/path/to/ORBIS

# One-time
cp .env.example .env                             # edit LLM_URL if running locally
cp config/orbis.example.yaml config/orbis.yaml   # optional; wizard writes it
cp config/users.example.yaml config/users.yaml   # tailnet only

# Run dev
python app.py                                    # :7866 (sidecar serves SPA at /)
# Or with hot-reload during frontend dev:
cd web && bun install && bun run dev             # :5173

# First-run: the wizard appears. To re-run it:
# in browser console: localStorage.removeItem('orbis.setupComplete'); location.reload()

# Tests
python -m pytest                                  # 470+ passing
python -m pip install -e '.[test]'                # respx for LLM-probe tests
python -m pip install -e '.[whisper]'             # opt-in to in-process Whisper
```

## Known tripwires (don't change lightly)

Carried forward from the protoVoice seed — hard-won discoveries:

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
- **Preset gradient swatch is NOT aria-hidden** — it wraps the Preview
  button, which needs to stay in the a11y tree.

## One-line rollback

The repo history doesn't carry tags yet. The initial squashed seed
is commit `25bcc9d` (the very first); checking out that commit puts
you back to the unmodified protoVoice v0.12.1 seed before the
demolition.

```bash
git checkout 25bcc9d    # seed state, pre-carve
```
