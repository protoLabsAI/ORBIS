# STATUS — current snapshot

*Last updated 2026-04-28 (architectural redirect: drop web target, Apple-Silicon-only). Branch: `main`.*

This file is a point-in-time pickup doc. Always up-to-date; read this
first on any resume before digging into code.

---

## Direction (locked 2026-04-28)

**ORBIS targets Apple Silicon Mac as the only first-class platform; iOS / iPad is the planned secondary target. Web / PWA / browser is dropped entirely.**

The dual-transport `AUDIO_TRANSPORT=native|webrtc` toggle goes away — there is one transport. See [`DECISIONS.md` § "Apple Silicon (+ iOS planned) only" amendment (2026-04-28)](./DECISIONS.md) and [`docs/native-audio-direction.md`](./docs/native-audio-direction.md) for the comprehensive guide.

The migration is staged in four phases:

1. **Strip web** — **DONE 2026-04-28.** Deleted WebRTC client deps, PWA service worker, `getUserMedia` paths, multi-input mixer, transport factory, `/api/offer`, `media_permission_patch.m`, plus 7 ROI-ranked Phase-1 sub-items below. **−1,391 LoC net** in the working tree, **−442 kB off the JS bundle** (1,962 → 1,520 kB).
2. **Apple-native audio** (1–2 weeks). Replace CPAL input + custom `aec.rs` with `AVAudioEngine` voice-processing IO via `objc2-avf-audio` (already a transitive dep via cpal 0.17). Apple ships AEC + AGC + NS tuned per Mac model. Today's 8× software-mic-gain hack and STT_MIN_RMS gates dissolve.
3. **protoApp consolidation** (Q2). Adopt `protolabs-voice-core` from `protoLabsAI/protoApp` as the shared Rust audio + inference substrate. ORBIS becomes a Python sidecar speaking the `orbis-sidecar` WebSocket contract.
4. **iOS** (Q3+). Full migration to in-process Rust (`whisper-rs`, `kokoros`, `llama-cpp-2`). Python sidecar becomes desktop-only optional.

---

## Phase 1 — what shipped (2026-04-28)

All 11 Phase-1 ROI-ranked items from `docs/native-audio-direction.md`, except items 2 (`webrtc-audio-processing 2.0.4`) and 7 (rubato `FftFixedIn` outside the audio callback) which are deferred — both get superseded by Phase 2's AVAudioEngine voice-processing IO so the work would be thrown away.

| # | Action | Commit |
|---|---|---|
| 9 | `enable_rtvi=False` on `PipelineTask` (silences boot warning) | `23bd14e` |
| 10 | CASTER 20-channel broadcast bug fix (mono → frame[0] only) | `c0a62b8` |
| 11–13, 8 | Strip web/PWA target — Python side / frontend side / Tauri media-permission shim / PWA selfDestroying | `91b77f0` `…` `04500e2` |
| 6 | `SseBusObserver` → subclass `RTVIObserver` (stop forking RTVI vocab) | `1060f1e` |
| 1 | `Webview::clear_all_browsing_data()` IPC + Diagnostics settings panel button | `6407a63` |
| 3 | `cpal 0.15.3 → 0.17.3`; drop `unsafe impl Send for AudioEngine` | `83a7a92` |
| 4 | Ad-hoc codesign with stable `--identifier studio.protolabs.orbis` (TCC stability) | `e6676af` |
| 5 | `tauri-plugin-log 2.x` — unify Rust + sidecar + frontend log streams | `96e5442` |

### Today's voice-loop band-aids (still in the tree, deliberately)

These get superseded in Phase 2 (AVAudioEngine gives us real AEC + AGC). They're not bugs to fix — they're load-bearing today and the comments in code reference Phase 2 as the proper supersession.

- VAD: `confidence=0.85→0.7`, `min_volume=0.75→0.2` (compensates for M1 mic delivering ~0.013 RMS raw)
- STT-side hallucination filters: phrase blocklist, `STT_MIN_RMS=0.07`, `STT_MIN_TEXT_LEN=10`, `STT_STRONG_RMS=0.15` in `voice/stt.py`
- 8× software mic gain in `voice/local_transport.py` (`_apply_gain_i16`)
- Python echo guard at 800ms (was disabled on the incorrect "Rust handles AEC" assumption)
- Backchannel + MicroAck default-off (false-trigger on bot's TTS bleed without real AEC)
- `cancel_on_idle_timeout=False` on `PipelineTask` (Pipecat's 5-min default was tearing down the persistent pipeline mid-wizard)
- Filler/backchannel routes to persona LLM (was hardcoded to `LLM_URL` env defaulting to `localhost:8100/v1`)

### Tooling

- `scripts/nuke-and-rebuild.sh` (~70-80s end-to-end full clean rebuild + launch). Wipes web/dist, dist-sdist, src-tauri bundle, sidecar binary, `~/Library/Application Support/pyapp/orbis`, sidecar.log, `/tmp/pyapp-build-fix`, all `/tmp/orbis-audio-*.sock`, AND WebKit + HTTPStorages dirs for both `studio.protolabs.orbis` AND `orbis-tauri` bundle IDs. Ad-hoc codesigns the bundle with `--identifier studio.protolabs.orbis` so TCC stays stable across rebuilds.
- `.claude/skills/orbis-rebuild-install/SKILL.md` — the script as a project-level Claude skill.
- Settings panel → Diagnostics → "Clear browsing data" button calls `Webview::clear_all_browsing_data()` IPC (in-process equivalent of the script's WebKit wipe; doesn't require a rebuild).
- `CLAUDE.md` — agent operating notes incl. nuke-and-rebuild workflow, the (now-unified) log file at `~/Library/Logs/studio.protolabs.orbis/orbis.log`, diagnosis checklist for "voice doesn't work".

### Lessons memorialized

- **PWA service worker + WKWebView state outlive builds.** Phase 1 replaces this with `Webview::clear_all_browsing_data()`; the script's offline rm -rf stays as the rebuild-path fallback.
- **M1 internal mic without AGC is too quiet for default VAD.** RMS ~0.013 raw. Software gain is the band-aid until Phase 2 swaps to AVAudioEngine voice-processing.
- **Filler controllers had their own LLM_URL env var** independent of persona config — split-brain. Routes to persona LLM now.
- **Idle-timeout default kills the persistent-pipeline pattern.** `cancel_on_idle_timeout=False` is mandatory for the always-on CPAL path.
- **The Tauri-spawned binary used to have TWO bundle IDs** depending on launch path (`open ORBIS.app` → `studio.protolabs.orbis`; running `orbis-tauri` directly → `orbis-tauri`). Phase 1 ad-hoc signing with stable `--identifier` collapsed this to one TCC identity.

---

## Repo state

- **Branch:** `main` (no release tag cut for today's working-tree changes yet)
- **Tests:** 493 passing, 2 skipped (`pytest`), zero failures
- **Build:** `scripts/nuke-and-rebuild.sh --launch --tail` is the supported dev loop
- **Live verified:** voice loop functional end-to-end with the today-band-aids in place; native CPAL mode confirmed via `/healthz` (`audio.transport: native`)
- **Release pipeline:** `.github/workflows/` retargeted to `protoLabsAI/ORBIS`; v0.1.10 was last tagged. Desktop-build workflow produces signed + notarized .dmg via App Store Connect API key

---

## TL;DR (product)

ORBIS is a voice-first AI companion — an orb that talks back in real time, remembers you across sessions, and delegates heavy reasoning to your configured agents. Single-owner, tailnet-hostable, SQLite-backed memory + personality, pipecat voice pipeline with kokoro default TTS.

Apple Silicon Mac is the only supported desktop today; iOS is the planned secondary target. The Python sidecar pattern stays through Phase 1+2 then migrates to a WebSocket contract over `protolabs-voice-core` in Phase 3.

Whisper transcribes (~250ms), MLX-LM or remote gateway replies (~350ms TTFB on the in-process MLX path), Kokoro speaks back (0.16× realtime). First-audio-out ~1.0–1.2s per turn on M1 Pro 32GB.

---

## Where we are

### Codebase

- **Repo:** [github.com/protoLabsAI/ORBIS](https://github.com/protoLabsAI/ORBIS)
- **Sibling repo (Phase 3 target):** [github.com/protoLabsAI/protoApp](https://github.com/protoLabsAI/protoApp) — has `protolabs-voice-core` (in-process Rust voice substrate) and `orbis-sidecar` crate (WS contract for Python sidecars)
- **Branch:** `main` (no release tag cut yet for today's changes)
- **Tests:** 493 passing, 2 skipped, zero failures

### What's shipped (still applies, modulo Phase 1 deletes)

**Backend spine**
- Single-persona loader (`config/orbis.yaml`) with `persona`, `voice`, `llm`, and `orb` blocks. Env overrides per-field; persona wins when explicitly set
- Single-owner API-key auth; tailnet-hosted multi-device by design
- Pipecat 1.1.0 voice pipeline; kokoro default TTS
- SQLite memory — sessions (FTS5), facts (bi-temporal + 90-day half-life decay), personality axes, mood, entitlement cache
- Personality rendering into prompt + post-session drift analyzer
- Soft-neglect mood shifts over days of silence
- Tool surface: `delegate_to` + `adjust_personality` (orb-control tools removed; handled via external process signals)
- TTS pluggable: kokoro / openai-compat / elevenlabs / fish
- LLM factory (`voice/llm/`) — pluggable adapters: OpenAI-compat, Ollama-native, MLX-LM in-process for Apple Silicon
- LLM-endpoint probing (test, model list, local auto-detect, MLX HF-id validation)

**Desktop shell (Tauri 2 + Mac signing)**
- Tauri 2.10.3 shell with PyApp-bundled Python sidecar. Apple Silicon arm64 is the only supported target.
- WKWebView WebContent media-capture works via the Obj-C shim `mic_permission.m` (TCC registration via `AVCaptureDevice`). The `media_permission_patch.m` runtime UIDelegate swap **gets deleted in Phase 1** — irrelevant once getUserMedia is gone.
- Hardened-runtime entitlements: `device.audio-input`, `device.camera`, network, JIT, `disable-library-validation`
- CI builds Developer-ID-signed + notarized .dmg via App Store Connect API key

**Voice loop benchmarks** — Apple M1 Pro 10-core 32GB, 10-turn run
- STT (Whisper-base.en): 244ms p50 for 3s clip
- LLM TTFB (MLX Qwen3.5-4B 4-bit): 327ms p50, 422ms p95
- LLM decode: 45 tok/s steady-state
- TTS (Kokoro): 294ms TTFA p50, 0.13× RTF
- End-to-end first-audio-out: ~1.0s per turn

**API surface** (`/api/*`, auth-gated except where noted) — **`/api/offer` (WebRTC signalling) goes in Phase 1.**

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
| `events` | GET | ✓ | SSE stream: `bot-state`, `transcript`, `session` events |
| `metrics` | GET | ✓ | Counters |
| `healthz` | GET | — | Process shape |
| ~~`offer`~~ | ~~POST/PATCH~~ | ~~✓~~ | **Phase 1 delete — WebRTC signalling, no longer used** |

**Frontend (React + Vite + shadcn)**
- First-run setup wizard (welcome → names → llm → pick → done → hatch). Phase 1 simplifies: voiceprint enrollment step gets removed, `MicTest.tsx`/`recordWav.ts` getUserMedia paths deleted, NativeLevelMeter (already calls Tauri IPC) is the only mic-test surface.
- Drawer with Voice + Orb tabs.
- Mood polling plugin — subscribable via `useMood()`.
- Orb plugin system (Fractal / Nebula / Crystal / Particles).

---

## Pending follow-ups (mapped to phases)

### Phase 1 — DONE 2026-04-28

All ROI-ranked items shipped except #2 (`webrtc-audio-processing`) and #7 (rubato `FftFixedIn` outside callback). Both deliberately deferred — Phase 2's AVAudioEngine adoption supersedes them, and integrating webrtc-audio-processing's C++ build dep + writing fresh resampler glue would be thrown-away work in 1-2 weeks. The 8× software-mic-gain hack and STT_MIN_RMS gates remain in place as load-bearing band-aids until Phase 2 lands.

### Phase 2 (next, 1–2 weeks)

- Migrate input from CPAL → `AVAudioEngine` voice-processing IO via `objc2-avf-audio` (already a transitive dep via cpal 0.17 — pulled in for free).
- Delete `aec.rs` entirely (Apple ships AEC + AGC + NS tuned per Mac model; WWDC23-10235).
- Delete the `MIC_GAIN`, `STT_MIN_RMS`, `STT_STRONG_RMS`, `STT_MIN_TEXT_LEN` knobs.
- Re-enable backchannel + microack now that real AEC is in place.
- VAD back to defaults (`confidence=0.7, min_volume=0.6`).

### Phase 3 (Q2, weeks)

- Vendor `protolabs-voice-core` from `protoLabsAI/protoApp` as a Cargo dep
- Migrate Python sidecar to speak `orbis-sidecar`'s WebSocket contract
- Delete `voice/local_transport.py`, `src-tauri/src/audio/socket.rs`, `voice/native_bargein.py`, `voice/sse_bus.py`

### Phase 4 (Q3+)

- iOS / iPad target via Tauri Mobile
- Full Rust in-process LLM/STT/TTS per protoApp
- Python sidecar becomes desktop-only optional

### Pre-existing follow-ups (not blocking)

- **Per-variant mood visual mapping** — `moodStore` polls, but no variant subscribes yet. Each orb shader needs its own mood → uniform translation
- **State + mood authoring editor** — drag-a-slider-see-the-orb-react surface for users to author their own state/mood mappings. Paid-tier feature per DECISIONS.md amendment
- **`_active_skill()` naming rename** — compat shim returning Persona; cosmetic
- **Docker hostname resolution UX** (task #68) — wizard accepts bare hostnames like `ava` that don't resolve inside containers
- **ACP / MCP / CLI subprocess delegates** — scoped out; users wrap their CLI in A2A themselves

See [HANDOFF.md](./HANDOFF.md) for the full QA checklist + open design questions.

---

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
  tools.py                     delegate_to + adjust_personality
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
memory/                        SQLite memory backend
voice/                         STT + TTS pipecat adapters + native audio transport
  transport_factory.py         Phase 1 — DELETE (factory branching)
  local_transport.py           LocalAudioInputTransport / Output (CPAL path) — kept through Phase 2; deleted Phase 3
  sse_bus.py                   SseBus singleton; /api/events fan-out — kept through Phase 2; deleted Phase 3
  native_bargein.py            NativeBargeInObserver — Phase 1 simplifies (drop WebRTC branch); Phase 3 deletes
  tee_processor.py             TeeFrameProcessor; LocalAudioOutputSink — Phase 1 simplifies (drop WebRTC sink)
  multi_input_mixer.py         Phase 1 — DELETE (CPAL+WebRTC arbitration only)

src-tauri/src/audio/           Rust CPAL engine
  engine.rs                    CPAL streams (kept through Phase 1; input migrates to AVAudioEngine in Phase 2)
  socket.rs                    Unix socket protocol (kept through Phase 2; deleted Phase 3 → WebSocket)
  aec.rs                       Phase 1 swap → webrtc-audio-processing; Phase 2 delete

src-tauri/src/                 Tauri shell
  mic_permission.m             AVCaptureDevice TCC registration (kept)
  media_permission_patch.m     Phase 1 — DELETE (WKUIDelegate Grant→Prompt swap, dead without getUserMedia)

web/src/
  App.tsx                      side-effect imports; top-level PipecatClient (Phase 1: WebRTC client deletes)
  voice/                       pipecat client + state bridge
    VoiceStateBridge.tsx       Phase 1 simplifies (drop WebRTC branches)
    useNativeBridge.ts         Phase 1 → promote to default, rename useVoiceBridge.ts
    state.ts                   Phase 1: drop audioTransport field (always native)
  components/Drawer.tsx
  plugins/
    orb/                       R3F orb + variants + store
    orb-settings/
    voice-panel/
    status-pill/               Phase 1 simplifies (drop WebRTC connection state)
    setup-wizard/              Phase 1: voiceprint step removed
    mood/
  shared/audio/
    NativeLevelMeter.tsx       (kept; the only mic-test surface after Phase 1)
    MicTest.tsx                Phase 1 — DELETE (getUserMedia)
    recordWav.ts               Phase 1 — DELETE (getUserMedia)
  auth/
  lib/api.ts                   Phase 1: drop /api/offer wrapper

config/                        user-editable YAML
tests/                         pytest (test count current; Phase 1 deletes test_multi_input_mixer + WebRTC offer tests)
docs/
  native-audio-direction.md    Phase 1+ comprehensive guide (this is the source of truth)
  native-audio-transport.md    Phase 1–5 architecture historical (will be archived)
  orb-visualizer.md
scripts/
  nuke-and-rebuild.sh          full nuke + rebuild + launch (the dev loop)
  build-desktop-binary.sh      sidecar-only build path
```

---

## Quick-start

```bash
cd ~/path/to/ORBIS

# One-time
cp .env.example .env                             # edit LLM_URL if running locally
cp config/orbis.example.yaml config/orbis.yaml   # optional; wizard writes it
cp config/users.example.yaml config/users.yaml   # tailnet only

# Dev loop (the supported one)
./scripts/nuke-and-rebuild.sh --launch --tail

# Tests
python -m pytest                                  # 493 passing, 2 skipped
python -m pip install -e '.[test]'                # respx for LLM-probe tests
```

---

## Known tripwires (don't change lightly)

Carried forward; updated 2026-04-28 with today's lessons.

- **`append_to_context=False`** on every out-of-band TTSSpeakFrame (filler, backchannel, delivery). Without it the LLM riffs on its own fillers.
- **`cancel_on_interruption=True`** default for sync tools.
- **`cancel_on_idle_timeout=False` in native mode.** Pipecat's 5-min default tears down the persistent pipeline mid-wizard. *(2026-04-28 today)*
- **Filler/backchannel LLM URL must follow the persona.** Hardcoded `LLM_URL` env var defaults to `localhost:8100/v1` and spams connection-error retries forever if the user isn't running vLLM there. *(2026-04-28 today)*
- **M1 internal mic without AGC delivers ~0.013 RMS for normal speech.** Until Phase 2 (AVAudioEngine), `MIC_GAIN=8` software boost in `voice/local_transport.py` is required. *(2026-04-28 today)*
- **WebView state outlives builds.** `~/Library/WebKit/<bid>/` and `~/Library/HTTPStorages/<bid>*/` for both bundle IDs (`studio.protolabs.orbis` AND `orbis-tauri`) cache stale frontend bundles and intercept `/api/*` fetches with "Load failed". `scripts/nuke-and-rebuild.sh` wipes them; Phase 1 replaces with `Webview::clear_all_browsing_data()`. *(2026-04-28 today)*
- **Whisper hallucinates on silence/breath/clicks** — "thanks for watching", "you", ".com", Korean phrases. STT-side phrase blocklist + `STT_MIN_RMS` gate filters them. *(2026-04-28 today; goes away in Phase 2 with Apple's NS)*
- **Backchannel + MicroAck are off by default in native mode.** Speaker bleed + 8× mic gain false-trigger them on the bot's own tail. Re-enable in Phase 2 once real AEC lands. *(2026-04-28 today)*
- **Browser mic constraints stay at defaults** (AGC/NS/EC on) — relevant only through Phase 1; deleted thereafter.
- **Fractal orb rotation + uTime wrap at 2π·N** to avoid float32 precision drift after ~10 min.
- **FTS5 is required** in the SQLite build — ORBIS refuses to start without it.
- **Stripe webhook endpoint is unauth** on purpose — signature verification is the auth.
- **`_active_skill()` is a compat shim** returning the Persona. The name is stale.

---

## One-line rollback

The repo history doesn't carry tags yet. The initial squashed seed is commit `25bcc9d` (the very first); checking out that commit puts you back to the unmodified protoVoice v0.12.1 seed before the demolition.

```bash
git checkout 25bcc9d    # seed state, pre-carve
```
