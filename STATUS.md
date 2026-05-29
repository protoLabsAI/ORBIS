# STATUS — current snapshot

*Last updated 2026-05-29 (native fork selective upstream port). Branch:
`tori/canon-native-mac`, pushed to `protoLabsAI/orbis-native:main`.*

This file is a point-in-time pickup doc. Always up-to-date; read this
first on any resume before digging into code.

---

## Direction (locked 2026-04-28)

**ORBIS starts with Apple Silicon Mac as the production desktop target; Linux and Windows desktop support are intentionally sequenced after the Mac native-audio build is stable. iOS / iPad remains a planned secondary target. Web / PWA / browser is dropped entirely.**

The dual-transport `AUDIO_TRANSPORT=native|webrtc` toggle goes away — there is one transport. See [`DECISIONS.md` § "Mac-first desktop, Linux/Windows later" amendment (2026-05-29)](./DECISIONS.md) and [`docs/native-audio-direction.md`](./docs/native-audio-direction.md) for the comprehensive guide.

## Native fork status — 2026-05-29

Canonical native work now lives in
[`protoLabsAI/orbis-native`](https://github.com/protoLabsAI/orbis-native).
This repo is intentionally Tauri-first: upstream `protoLabsAI/ORBIS`
remains useful as a source of product/backend changes, but its current
PWA/WebRTC direction is not a merge target.

Selective upstream work already brought forward into `orbis-native:main`:

- Native/Tauri canon preserved: `src-tauri`, native audio transport,
  macOS permission IPC, release scripts, native tests, and native docs stay.
- Runtime settings carried forward: OpenAI-compatible LLM custom URL,
  STT runtime settings, TTS endpoint settings, native audio runtime controls,
  simplified LLM provider display, and collapsible settings sections.
- Delegation/agent hardening carried forward: delegate UI/API/health,
  A2A auth hardening, inbox ingress, tool-call translation, delegation
  progress, micro-ack timing hardening, and personality drift fallback metrics.
- Observability carried forward/adapted: enriched Langfuse turn spans,
  web build CI, backend pytest CI, and a native event log drawer that tails
  Tauri API calls plus SSE-derived voice state.
- Voice persona prompt carried forward/adapted: the default voice-first
  prompt now lives in `config/persona.md`, and the Tauri first-run seed bundles
  and copies it next to `orbis.yaml` so packaged installs resolve
  `system_prompt_file` correctly.

Intentionally skipped from upstream unless redesigned for native:

- Hosted PWA/split-deployment connection flow, pairing, browser
  `getUserMedia`, Pipecat WebRTC client, Document PiP, PWA Vite/service-worker
  pieces, and browser mic-permission rationale components.
- Upstream deletes of `src-tauri`, native audio scripts/tests/docs, and
  `voice/local_transport.py` / `voice/native_bargein.py` / `voice/sse_bus.py`.
- OpenAPI generated client drift that assumes the hosted SPA path instead of
  the existing `@tauri-apps/plugin-http` wrapper.

Current local validation on this fork:

- `uv run --extra test pytest -q` passed: 646 tests, 2 skipped.
- `cd web && bun run build` passed; Vite still reports the existing large
  bundle warning.
- Changed-file ESLint for the new native event-log slice passed. Full
  `bun run lint` is not a clean gate yet because of pre-existing repo-wide
  lint debt in orb/settings/voice code.
- `scripts/check-macos-release-config.py` passed after each native-safe slice.

The migration is staged in four phases:

1. **Strip web** — **DONE 2026-04-28.** Deleted WebRTC client deps, PWA service worker, `getUserMedia` paths, multi-input mixer, transport factory, `/api/offer`, `media_permission_patch.m`, plus 7 ROI-ranked Phase-1 sub-items below. **−1,391 LoC net** in the working tree, **−442 kB off the JS bundle** (1,962 → 1,520 kB).
2. **Apple-native audio** — **IN PROGRESS / production hardening 2026-05-29.** Production macOS builds now use `native-audio,voice-processing`, request/check macOS microphone permission explicitly, default the sidecar to Apple-AGC-safe unity mic gain, ship DMG artifacts, and verify source + built `.app` microphone metadata in CI. Live Apple Silicon audio soak still gates declaring this phase complete.
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

These are being retired behind the production `voice-processing` path, but remain as fallback/sidecar tuning until live Apple Silicon soak proves the AVAudioEngine path across first-run, denied-permission, and noisy-room scenarios.

- VAD: `confidence=0.85→0.7`, `min_volume=0.75→0.2` (compensates for M1 mic delivering ~0.013 RMS raw)
- STT-side hallucination filters: phrase blocklist, `STT_MIN_RMS=0.07`, `STT_MIN_TEXT_LEN=10`, `STT_STRONG_RMS=0.15` in `voice/stt.py`
- Software mic gain in `voice/local_transport.py` (`_apply_gain_i16`) for legacy CPAL input only; macOS voice-processing defaults to `MIC_GAIN=1.0`
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
- **M1 internal mic without AGC is too quiet for default VAD.** RMS ~0.013 raw. Software gain remains a CPAL-only band-aid; the production macOS voice-processing path lets Apple AGC own normalization.
- **Filler controllers had their own LLM_URL env var** independent of persona config — split-brain. Routes to persona LLM now.
- **Idle-timeout default kills the persistent-pipeline pattern.** `cancel_on_idle_timeout=False` is mandatory for the always-on CPAL path.
- **The Tauri-spawned binary used to have TWO bundle IDs** depending on launch path (`open ORBIS.app` → `studio.protolabs.orbis`; running `orbis-tauri` directly → `orbis-tauri`). Phase 1 ad-hoc signing with stable `--identifier` collapsed this to one TCC identity.

---

## Repo state

- **Branch:** `main` (no release tag cut for today's working-tree changes yet)
- **Tests:** focused native-audio host checks pass (`tests/test_local_transport.py`,
  `tests/test_healthz_native_audio.py`, and Tauri Rust tests with
  `native-audio,voice-processing`). Run the full suite before cutting a release.
- **Build:** `scripts/nuke-and-rebuild.sh --launch --tail` is the supported dev loop
- **Live verified:** historical native CPAL loop functional end-to-end with the old band-aids in place; current AVAudioEngine voice-processing production path still needs Apple Silicon soak evidence.
- **Release pipeline:** `.github/workflows/` retargeted to `protoLabsAI/ORBIS`; v0.1.10 was last tagged. Desktop-build workflow is Mac-first, builds signed + notarized DMGs via App Store Connect API key on semver tags, and fails if the DMG cannot be attached to the GitHub Release.

---

## TL;DR (product)

ORBIS is a voice-first AI companion — an orb that talks back in real time, remembers you across sessions, and delegates heavy reasoning to your configured agents. Single-owner, tailnet-hostable, SQLite-backed memory + personality, pipecat voice pipeline with kokoro default TTS.

Apple Silicon Mac is the supported desktop build we are hardening first; Linux and Windows desktop support come after that. iOS is the planned secondary target. The Python sidecar pattern stays through Phase 1+2 then migrates to a WebSocket contract over `protolabs-voice-core` in Phase 3.

Whisper transcribes (~250ms), MLX-LM or remote gateway replies (~350ms TTFB on the in-process MLX path), Kokoro speaks back (0.16× realtime). First-audio-out ~1.0–1.2s per turn on M1 Pro 32GB.

---

## Where we are

### Codebase

- **Repo:** [github.com/protoLabsAI/ORBIS](https://github.com/protoLabsAI/ORBIS)
- **Sibling repo (Phase 3 target):** [github.com/protoLabsAI/protoApp](https://github.com/protoLabsAI/protoApp) — has `protolabs-voice-core` (in-process Rust voice substrate) and `orbis-sidecar` crate (WS contract for Python sidecars)
- **Branch:** `main` (no release tag cut yet for today's changes)
- **Tests:** run the full release-candidate suite before tagging; current
  host-side native-audio verification is listed in Repo state above.

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
- Tauri 2.10.3 shell with PyApp-bundled Python sidecar. Apple Silicon arm64 is the current production target.
- Native audio is required in production desktop builds. The Rust shell gates sidecar startup on macOS microphone authorization, exposes permission status/request/settings IPC, and uses AVAudioEngine voice-processing input for AEC + AGC + noise suppression when built with `voice-processing`. That build passes `ORBIS_AUDIO_INPUT_MODE=voice_processing` to the sidecar so `MIC_GAIN` defaults to 1.0 unless explicitly overridden.
- The frontend asks the shell for the active input mode. macOS voice-processing uses the current system input and does not show the legacy CPAL device picker; CPAL builds still expose selectable input devices.
- Hardened-runtime entitlements: `device.audio-input`, network client/server, narrow JIT exception for WKWebView. Camera and broader code-signing exceptions are intentionally absent.
- CI builds Developer-ID-signed + notarized `.dmg` via App Store Connect API key. Tag builds fail unless signing secrets are present, then verify source plists, built `.app` metadata, arm64 main executable, bundled PyApp sidecar, embedded signed entitlements, Gatekeeper assessment, stapled notarization tickets for both `.app` and `.dmg`, and run `scripts/validate-macos-native-audio.sh --release` with the report uploaded as a workflow artifact. The release harness now repeats the signing, Gatekeeper, stapler, and narrow-entitlement checks on the `ORBIS.app` mounted from the DMG, so the installed payload is verified, not just the build-tree app.

**Voice loop benchmarks** — Apple M1 Pro 10-core 32GB, 10-turn run
- STT (Whisper-base.en): 244ms p50 for 3s clip
- LLM TTFB (MLX Qwen3.5-4B 4-bit): 327ms p50, 422ms p95
- LLM decode: 45 tok/s steady-state
- TTS (Kokoro): 294ms TTFA p50, 0.13× RTF
- End-to-end first-audio-out: ~1.0s per turn

**API surface** (`/api/*`, auth-gated except where noted). `/api/offer` is gone with the WebRTC path.

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
- First-run setup wizard (welcome → names → llm → pick → microphone → done → hatch). The microphone step now uses Tauri IPC for macOS permission status/request/settings plus the native RMS meter.
- Drawer with Voice + Orb tabs.
- Mood polling plugin — subscribable via `useMood()`.
- Orb plugin system (Fractal / Nebula / Crystal / Particles).

---

## Pending follow-ups (mapped to phases)

### Phase 1 — DONE 2026-04-28

All ROI-ranked items shipped except #2 (`webrtc-audio-processing`) and #7 (rubato `FftFixedIn` outside callback). Both deliberately deferred — Phase 2's AVAudioEngine adoption supersedes them, and integrating webrtc-audio-processing's C++ build dep + writing fresh resampler glue would be thrown-away work in 1-2 weeks. The software-mic-gain hack now remains only for the legacy CPAL path; the macOS voice-processing path defaults to unity gain. STT_MIN_RMS gates stay in place until live Apple Silicon soak proves they can be removed.

### Phase 2

**Phase 2a — DONE 2026-04-28.** `AVAudioEngine` voice-processing input lands in `src-tauri/src/audio/voice_processing_input.rs` behind the `voice-processing` Cargo feature.

**Phase 2 production hardening — IN PROGRESS 2026-05-29.** macOS CI, release builds, the PR/main preflight workflow, and the local clean rebuild script now enable or verify `native-audio,voice-processing`, gate startup on macOS microphone permission, expose permission IPC to the wizard/settings panel, default voice-processing input to unity gain, hide the legacy CPAL device picker for the macOS system-input voice-processing path, ship DMG artifacts, assert source + built `.app` microphone metadata, and gate signed tag artifacts on Developer ID/Gatekeeper/stapler checks. The desktop release job waits for the parallel Docker release workflow and fails if the signed DMG cannot be attached to the GitHub Release. The preflight workflow includes focused Python native-transport tests plus a macOS arm64 compile/test job for the Apple-specific AVAudioEngine path before release tags. The local rebuild script can also produce an unsigned DMG with `--dmg` for release-candidate packaging checks; it signs `ORBIS.app` first, stages it at the DMG volume root, then packages that staged signed app into the local DMG. `scripts/check-macos-release-config.py` is the host-portable static guardrail for source/workflow drift; `scripts/validate-macos-native-audio.sh` is the repeatable Apple Silicon live-validation harness and now truncates logs before launch, verifies the app executable and bundled PyApp sidecar are arm64, verifies first-run config resources are bundled, validates manual/unsigned DMG payloads, verifies release mode includes a DMG, can validate a downloaded DMG without a separate built app, mounts the DMG to prove it contains `ORBIS.app` with the arm64 executable, sidecar, and resources, checks signing/notarization for both the build-tree app and the mounted DMG app, verifies signed entitlements stay narrow, proves launch logs show AVAudioEngine + sidecar readiness, non-silent microphone input while the tester speaks, Python transport connection to the native audio socket, Python-side mic frame receipt, Python-side speaker frame send, and Rust-side playback frame receipt, and verifies `/healthz` reports native transport, voice-processing input mode, unity mic gain, a configured plus currently connected native audio socket, a running native voice pipeline task, received mic frames, and sent speaker frames. Phase 1 CPAL band-aids stay available outside the voice-processing path until live Apple Silicon validation clears them.

**Phase 2b — pending live validation.** Build with the feature flag, run, verify, then ship the cleanup commit:

```bash
./scripts/nuke-and-rebuild.sh --launch --tail
# or
cargo tauri build --features native-audio,voice-processing --bundles app
# then
./scripts/validate-macos-native-audio.sh --launch --duration 240
```

Live validation playbook (test in this order, each gates the next):

1. **Boot succeeds.** Look for `[voice-processing] engine started — AEC + AGC + NS active` in `~/Library/Logs/studio.protolabs.orbis/orbis.log`. If `setVoiceProcessingEnabled(true)` fails, the engine refuses to start — fall back to the CPAL build for now and dig in from there.
2. **Mic levels are AGC-normalized.** Speak normally — the level meter in Settings → Mic should fill comfortably (not the today's-1-bar problem). Whisper transcripts should show `rms=0.05–0.15` range in the sidecar log, not the raw 0.013.
3. **Echo bleed no longer false-triggers VAD.** When the bot finishes speaking, no automatic `[backchannel] 'mm'` or `[micro-ack]` should fire on its own tail.
4. **Sustained turn-taking works.** Multi-turn conversation — verify the loop stays clean.

If all four pass, Phase 2b is the cleanup commit:
- Keep `--legacy-cpal` only if live validation finds a device-specific AVAudioEngine blocker; otherwise remove the fallback.
- Delete `src-tauri/src/audio/aec.rs` (187 LoC; Apple supersedes).
- Delete the CPAL `build_input_stream` + `preferred_input_config` paths.
- Delete `voice/local_transport.py` `MIC_GAIN` + `_apply_gain_i16` (Apple AGC supersedes).
- Delete `voice/stt.py` `STT_MIN_RMS` / `STT_STRONG_RMS` / `STT_MIN_TEXT_LEN` gates (Apple NS + Whisper handle silence properly without).
- In `app.py`, drop the `if AUDIO_TRANSPORT == "native": bc_cfg["enabled"] = False` block — backchannel + microack come back online.
- VAD back to pipecat defaults (`confidence=0.7, min_volume=0.6`).

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
  local_transport.py           LocalAudioInputTransport / Output over Unix socket; voice-processing mode uses unity mic gain
  sse_bus.py                   SseBus singleton; /api/events fan-out — kept through Phase 2; deleted Phase 3
  native_bargein.py            NativeBargeInObserver — flushes Rust playback over the native socket; Phase 3 deletes
  stt.py / stt_sensevoice.py   local STT adapters and silence/hallucination gates
  llm/                         LLM factory + adapters
  tts/                         Kokoro / OpenAI-compatible / ElevenLabs / Fish adapters

src-tauri/src/audio/           Rust native audio engine
  engine.rs                    CPAL output + fallback input; Mac production input is AVAudioEngine voice-processing
  socket.rs                    Unix socket protocol (kept through Phase 2; deleted Phase 3 → WebSocket)
  voice_processing_input.rs    macOS AVAudioEngine voice-processing microphone path
  aec.rs                       legacy CPAL fallback AEC; Phase 2 cleanup deletes after live soak

src-tauri/src/                 Tauri shell
  mic_permission.m             AVCaptureDevice TCC registration (kept)
  media_permission_patch.m     deleted (WKUIDelegate Grant→Prompt swap, dead without getUserMedia)

web/src/
  App.tsx                      desktop UI root; audio runs in native shell/sidecar
  voice/                       SSE-backed voice state bridge
    VoiceStateBridge.tsx       subscribes to native sidecar events
    useVoiceBridge.ts          default native bridge hook
    state.ts                   native voice UI state
  components/Drawer.tsx
  plugins/
    orb/                       R3F orb + variants + store
    orb-settings/
    voice-panel/
    status-pill/               Phase 1 simplifies (drop WebRTC connection state)
    setup-wizard/              first-run config + native microphone permission step
    mood/
  shared/audio/
    NativeLevelMeter.tsx       (kept; the only mic-test surface after Phase 1)
    microphonePermission.ts    Tauri mic permission IPC wrapper
    nativeAudio.ts             Tauri native audio mode/device wrapper
    preferredDevice.ts         legacy CPAL preferred-device storage
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
python -m pytest                                  # full release-candidate suite
python -m pip install -e '.[test]'                # respx for LLM-probe tests
```

---

## Known tripwires (don't change lightly)

Carried forward; updated 2026-04-28 with today's lessons.

- **`append_to_context=False`** on every out-of-band TTSSpeakFrame (filler, backchannel, delivery). Without it the LLM riffs on its own fillers.
- **`cancel_on_interruption=True`** default for sync tools.
- **`cancel_on_idle_timeout=False` in native mode.** Pipecat's 5-min default tears down the persistent pipeline mid-wizard. *(2026-04-28 today)*
- **Filler/backchannel LLM URL must follow the persona.** Hardcoded `LLM_URL` env var defaults to `localhost:8100/v1` and spams connection-error retries forever if the user isn't running vLLM there. *(2026-04-28 today)*
- **M1 internal mic without AGC delivers ~0.013 RMS for normal speech.** The legacy CPAL path still defaults to `MIC_GAIN=16`; the macOS voice-processing path sets `ORBIS_AUDIO_INPUT_MODE=voice_processing`, so `MIC_GAIN` defaults to 1.0 and lets Apple AGC own normalization. *(2026-05-29 update)*
- **WebView state outlives builds.** `~/Library/WebKit/<bid>/` and `~/Library/HTTPStorages/<bid>*/` for both bundle IDs (`studio.protolabs.orbis` AND `orbis-tauri`) cache stale frontend bundles and intercept `/api/*` fetches with "Load failed". `scripts/nuke-and-rebuild.sh` wipes them; Phase 1 replaces with `Webview::clear_all_browsing_data()`. *(2026-04-28 today)*
- **Whisper hallucinates on silence/breath/clicks** — "thanks for watching", "you", ".com", Korean phrases. STT-side phrase blocklist + `STT_MIN_RMS` gate filters them. *(2026-04-28 today; goes away in Phase 2 with Apple's NS)*
- **Backchannel + MicroAck are off by default in native mode.** Speaker bleed false-triggered them on the bot's own tail before AVAudioEngine AEC. Re-enable after live voice-processing soak proves tail suppression. *(2026-05-29 update)*
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
