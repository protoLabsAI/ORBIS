# STATUS — current snapshot

*Last updated 2026-04-24 (PR #28 + #30 voice / desktop arc). Branch: `main` + `feat/desktop-voice-followup` (PR #30 open).*

This file is a point-in-time pickup doc. Always up-to-date; read this
first on any resume before digging into code.

## TL;DR

ORBIS is a voice-first AI companion — an orb that talks back in real
time, remembers you across sessions, and delegates heavy reasoning to
your configured agents. Single-owner, tailnet-hostable, SQLite-backed
memory + personality, pipecat voice pipeline with kokoro default TTS.

**Desktop voice loop functional end-to-end on Apple Silicon.** Mic
permission lands via TCC (Developer-ID signed builds + a runtime
WKUIDelegate patch + an AVCaptureDevice TCC shim), audio reaches
Pipecat, Whisper transcribes (~250ms), MLX-LM in-process generates
the reply (~350ms TTFB, 42 tok/s decode on M1 base), Kokoro speaks
back (0.16× realtime). First-audio-out per turn ~1.0-1.2s on M1
base; scales ~2× per Apple-Silicon generation.

Repo is **runnable + tested live** — setup wizard ships end-to-end
on Mac desktop build, voice session connects + responds with the
full STT → LLM → TTS chain producing audio. 131 passing unit tests.

## Where we are

### Codebase

- **Repo:** [github.com/protoLabsAI/ORBIS](https://github.com/protoLabsAI/ORBIS)
- **Branch:** `main` (no release tag cut yet)
- **Tests:** 131 passing (`pytest`), zero failures
- **Build:** frontend bundles cleanly; Dockerfile restored + runnable
- **Live verified:** setup wizard + hatch animation + voice session
  round-trip confirmed working on first-user test box, AND in the
  Mac desktop build (Tauri shell + signed/notarized .dmg)
- **Release pipeline:** `.github/workflows/` retargeted to
  `protoLabsAI/ORBIS`; v0.1.10 tagged. Desktop-build workflow now
  produces signed + notarized .dmg via App Store Connect API key
  (PR #28 wired secrets through Infisical → GitHub Actions sync)

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

**Desktop shell (Tauri 2 + Mac signing)**
- Tauri shell with PyApp-bundled Python sidecar. Apple Silicon arm64
  is the supported desktop target; Linux/Windows builds remain in CI
  for completeness but are deprioritized (Docker self-host is the
  cross-platform answer per README).
- WKWebView WebContent media-capture works via two Obj-C shims:
  `mic_permission.m` (TCC registration via `AVCaptureDevice`) and
  `media_permission_patch.m` (runtime swap of wry's UIDelegate from
  Grant → Prompt so TCC actually gates the WebContent process).
- Hardened-runtime entitlements: `device.audio-input`,
  `device.camera`, network, JIT, `disable-library-validation`.
- CI builds Developer-ID-signed + notarized .dmg via App Store
  Connect API key. Secrets pulled from Infisical → GitHub Actions.

**Voice loop benchmarks (M1 base, current defaults)**
- STT (Whisper-base.en): 244ms p50 for 3s clip
- LLM TTFB (MLX Qwen3.5-4B 4-bit): 346ms p50, 419ms p95
- LLM decode: 42 tok/s steady-state
- TTS (Kokoro): 336ms TTFA p50, 0.16× RTF (6× faster than realtime)
- End-to-end first-audio-out: ~1.0-1.2s per turn
- Repeatable: `python scripts/bench.py --turns 10`

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
| `entitlement` | GET | ✓ | Paid-tier state |
| `entitlement/checkout` | POST | ✓ | Stripe Checkout session |
| `stripe/webhook` | POST | sig | Grant/revoke entitlement |
| `offer` | POST/PATCH | ✓ | WebRTC signalling |
| `metrics` | GET | ✓ | Counters |
| `healthz` | GET | — | Process shape |

**Frontend (React + Vite + shadcn)**
- First-run setup wizard (welcome → names → llm → pick → done → hatch)
  - Names: `persona.user_name` + `persona.name` collection
  - LLM: 15 provider presets (OpenAI / Anthropic / Groq / DeepSeek /
    OpenRouter / Together / Mistral / Fireworks / Moonshot / xAI /
    Ollama / LM Studio / vLLM / LiteLLM / Custom) + live model-list
    fetch + "Test connection" real round-trip + local-detect banner
    (emerald callout when Ollama or LM Studio is running on localhost)
  - Pick: 8 starter orbs, each with a palette-derived gradient swatch
    and a fullscreen preview modal (live shader, drag-to-rotate)
  - Hatch: 3.6s CSS reveal (seed → flare → fade)
- Drawer with Voice + Orb tabs
  - Voice tab: Agent (verbosity) / Profile (mood + axes + sessions
    + recent drift) / Access (owner API key)
  - Orb tab: variant / palette / param editing (gated by entitlement)
- Mood polling plugin — subscribable via `useMood()`
- Orb plugin system (Fractal / Nebula / Crystal / Particles)

### Config files shipped

- `config/orbis.example.yaml` — persona + voice + llm + orb
  (new llm block documents OpenAI / LiteLLM-gateway / local vLLM examples)
- `config/starter_orbs.yaml` — curated 8-orb pool
- `config/users.example.yaml` — owner credential shape
- `config/delegates.yaml` — A2A + OpenAI-compat targets
- `.env.example` — env vars (LLM, TTS, Stripe, tracing, etc.)

## Pending follow-ups (not blocking)

- **Per-variant mood visual mapping** — `moodStore` polls, but no
  variant subscribes yet. Each orb shader needs its own mood → uniform
  translation. Biggest remaining gap between designed and implemented.
- **State + mood authoring editor** — drag-a-slider-see-the-orb-react
  surface for users to author their own state/mood mappings. Paid-tier
  feature per DECISIONS.md amendment.
- **`_active_skill()` naming rename** — compat shim returning Persona;
  cosmetic but worth a rename pass.
- **Docker hostname resolution UX** (task #68) — wizard accepts bare
  hostnames like `ava` that don't resolve inside containers. Inline
  warning + docs note.
- **ACP / MCP / CLI subprocess delegates** — scoped out; users who
  want CLI-tool delegation wrap their CLI in A2A themselves
  (protoAgent is the reference). ORBIS stays voice-companion, not
  agent-framework.
- **Frontend CI** — no `bun run build` in the release pipeline yet.
- **Docs site rebuild** — VitePress was purged in the demolition;
  only `docs/orb-visualizer.md` survives. README + DECISIONS + STATUS
  + HANDOFF carry most of the load.

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
  App.tsx                      side-effect imports; top-level PipecatClient
  voice/                       pipecat client + state bridge
  components/Drawer.tsx        Sheet + Voice/Orb tabs
  plugins/
    orb/                       R3F orb + variants + store + broadcast bus
    orb-settings/              params editor (drawer Orb tab)
    voice-panel/               Agent + Profile + Access panels
    status-pill/               connection status indicator
    setup-wizard/              first-run flow + hatch animation
                                 - SetupWizard.tsx   (5-step flow)
                                 - OrbPreviewModal.tsx
                                 - paletteColors.ts
    mood/                      polling store + useMood() hook
  auth/                        apiKey store + useApiKey hook
  lib/api.ts                   typed /api/* fetch wrappers

config/                        user-editable YAML
tests/                         pytest: 131 cases
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
cd web && bun install && bun run dev             # :5173
python app.py                                    # :7866

# First-run: the wizard appears. To re-run it:
# in browser console: localStorage.removeItem('orbis.setupComplete'); location.reload()

# Tests
python -m pytest                                  # 131 passing
python -m pip install -e '.[test]'                # respx for LLM-probe tests
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
