# HANDOFF — ORBIS

*Updated 2026-05-29 (native fork selective upstream port). Canon branch:
`tori/canon-native-mac`, pushed to `protoLabsAI/orbis-native:main`.*

This doc is for the next human to sit down with ORBIS — whether
that's tomorrow-you, a teammate picking it up, or a handoff to a
contracted team. It covers: current state, a QA checklist broken
into verified / still-unverified, known issues, open design
questions, and ordered next steps.

[STATUS.md](./STATUS.md) has the point-in-time snapshot.
[DECISIONS.md](./DECISIONS.md) has the frozen architecture decisions
— read that first if you haven't. [README.md](./README.md) has the
developer-facing overview.
[`docs/native-audio-direction.md`](./docs/native-audio-direction.md)
is the comprehensive guide for the Mac-first native audio direction and
the 4-phase migration plan that supersedes the dual-transport
architecture.

## Current fork posture

`protoLabsAI/orbis-native` is the canonical Tauri-first fork. Treat upstream
`protoLabsAI/ORBIS` as a source of selective changes only. Do not merge
upstream `main` wholesale: it carries hosted PWA/WebRTC work and deletes the
native shell, native audio transport, Mac release scripts, and native tests.

What has been ported and pushed:

- Native/Tauri canon preserved and hardened.
- Runtime provider settings: custom LLM URL, STT/TTS runtime fields, native
  audio controls, simplified provider list, collapsible settings sections.
- Agent/delegation improvements: delegate CRUD/status/test UI, A2A auth,
  inbox ingress, tool-call translation, delegation progress, micro-ack timing,
  and drift fallback metrics.
- CI/observability: web build gate, backend pytest gate, enriched native turn
  spans, and a Dev drawer event log adapted for Tauri API calls plus SSE voice
  state.

What is intentionally not ported yet:

- PWA/split-deployment connect flow, pairing, browser `getUserMedia`, Pipecat
  WebRTC client, Document PiP, service-worker/Vite PWA pieces, and browser mic
  permission UI.
- Any upstream delete of `src-tauri`, native audio scripts/tests/docs, or the
  local native voice transport modules.
- Generated OpenAPI client changes until they are adapted to the native
  `@tauri-apps/plugin-http` API wrapper.

Validation already run on the native fork:

- `uv run --extra test pytest -q` passed: 646 tests, 2 skipped.
- `cd web && bun run build` passed. The existing Vite large-bundle warning
  remains.
- Changed-file ESLint for the native event-log slice passed. Full
  `bun run lint` still fails on pre-existing repo-wide lint debt.
- `scripts/check-macos-release-config.py` passed.

## Next-team handoff — Mac native audio

**Current state:** the repo is ready to pull onto an Apple Silicon Mac for
the final production proof pass. Off-Mac guardrails, CI/preflight coverage,
local rebuild scripts, release signing checks, DMG payload validation,
microphone permission IPC, and the AVAudioEngine voice-processing input path
are wired. Do not declare the Mac build production-ready until the evidence
below is captured from a real Mac.

**First commands on the Mac:**

```sh
cd ~/dev/ORBIS
scripts/preflight-native-audio-host.sh
scripts/check-macos-release-config.py
scripts/nuke-and-rebuild.sh --dmg
DMG="$(ls -t src-tauri/target/release/bundle/dmg/*.dmg | head -1)"
scripts/validate-macos-native-audio.sh --dmg "${DMG:?local DMG missing}"
scripts/validate-macos-native-audio.sh --launch --duration 240
```

During `--launch`, grant microphone access if prompted, speak normally, and
complete one short voice turn. The harness writes
`macos-native-audio-validation.txt`.

**Evidence required before calling Mac production-ready:**

- Local or CI `.dmg` contains `ORBIS.app` at the volume root.
- `ORBIS.app` executable and bundled `orbis-aarch64-apple-darwin` sidecar are
  arm64.
- `config/orbis.example.yaml` and `config/starter_orbs.yaml` are bundled.
- Mic permission prompt/status/settings IPC works without camera permission.
- Launch logs show `[voice-processing] engine started` and
  `[voice-processing] first input tap`.
- Launch logs show `[voice-processing] input became audible` while the tester
  speaks during the validation window.
- Sidecar logs show `audio_input_mode=voice_processing mic_gain=1.00`.
- Sidecar logs show `[local_transport] connected`.
- Sidecar logs show `[local_transport] first mic frame`.
- Sidecar logs show `[local_transport] first speaker frame` after a short
  voice turn.
- Rust logs show `[audio/socket] first playback frame received`.
- `/healthz` returns `status: ok`, `audio.transport: native`,
  `audio.input_mode: voice_processing`, `audio.mic_gain: 1.0`, and
  `audio.socket_configured: true`, `audio.socket_connected: true`, and
  `audio.pipeline_running: true`, with `audio.mic_frames_received > 0` and
  `audio.speaker_frames_sent > 0`.
- For release artifacts only:

```sh
scripts/validate-macos-native-audio.sh --release --dmg path/to/ORBIS.dmg
```

That additionally proves Developer ID signing, Gatekeeper assessment, stapled
notarization tickets, narrow entitlements, and downloaded-DMG validation.
Release mode checks both the build-tree `.app` and the `ORBIS.app` mounted
from the DMG, so the exact installed payload is covered even when validating
a downloaded artifact without a local build-tree `.app`.

**If validation fails:** keep `macos-native-audio-validation.txt`, the Rust
stderr path printed by the harness, and both files under
`~/Library/Logs/studio.protolabs.orbis/`. Do not start cleanup work that
deletes CPAL fallback paths or STT/VAD band-aids until the AVAudioEngine soak
passes.

**Worktree transfer note:** the native-fork work described above has been
committed and pushed to `protoLabsAI/orbis-native:main`. Start from a clean
checkout of that repo before continuing.

## 2026-04-28 update — direction change + Phase 1 complete

**ORBIS is Mac-first on the desktop, with Apple Silicon as the current
production target and Linux / Windows sequenced after the Mac native-audio
build stabilizes. iOS / iPad remains a planned secondary target. Web / PWA /
browser is dropped as a supported runtime.** See DECISIONS.md amendment of
the same date and `docs/native-audio-direction.md` for the comprehensive
guide.

**Phase 1 is done.** All 11 ROI-ranked items shipped today across 11
focused commits, except items 2 (`webrtc-audio-processing`) and 7
(rubato `FftFixedIn` outside callback) which are deliberately
deferred — Phase 2's AVAudioEngine adoption supersedes both. Net
**−1,391 LoC**, **−442 kB JS bundle**, the `unsafe impl Send for
AudioEngine {}` shim is gone, and the day-long voice loop debugging
that started the morning is captured as load-bearing band-aids in
the tree (VAD thresholds, legacy CPAL software mic gain, STT hallucination
filters). The current macOS voice-processing path defaults mic gain to unity;
the remaining cleanup waits for a signed DMG and live AVAudioEngine soak.
STATUS.md § "Phase 1 — what shipped" has the per-commit table.

## Context at a glance

- **Provenance:** Forked from
  [protoLabsAI/protoVoice](https://github.com/protoLabsAI/protoVoice)
  at v0.12.1, then demolished and rebuilt as ORBIS. The carve
  removed the skills catalog, multi-tenant auth, voice cloning, and
  Fish as default TTS. The rebuild added single-persona loading,
  SQLite memory, personality drift, soft-neglect, Stripe
  entitlement, setup wizard, orb self-modification tools, and a
  polished LLM-provider setup flow.
- **Product shape:** Voice-first AI companion. Single-owner.
  Tailnet-hostable. Router-first (delegates to the user's
  configured agents; doesn't try to be a framework itself).
  Differentiator is the *companion* layer: memory + personality +
  mood + soft-neglect.
- **Business model:** Free tier ships a complete product with a
  user-picked starter orb. Paid tier (one-time Stripe purchase,
  7-day offline-tolerant cache) unlocks full customization.
- **Status:** Architecture is locked. Engineering spine is complete.
  The Mac desktop build is in production hardening for native
  `native-audio,voice-processing`: CI, local rebuilds, signing
  guardrails, microphone permission IPC, and the Apple Silicon live
  validation harness are wired. The signed/notarized DMG plus live
  AVAudioEngine microphone soak still gates declaring the Mac build
  production-ready.

## What works — verified live

- Focused native-audio host checks pass:
  `tests/test_local_transport.py`, `tests/test_healthz_native_audio.py`, and
  Tauri Rust tests with `native-audio,voice-processing`
- `python app.py` boots cleanly
- Frontend builds + typechecks
- **Setup wizard runs end-to-end** (confirmed 2026-04-23 user test):
  welcome → names → llm → pick → done → hatch, writing
  `config/orbis.yaml` correctly at each step
- **Voice session connects + responds** with a real LLM + TTS
  configured via the wizard
- **Docker container restored** and bootable after the deps carve
- **Starter-orb preview modal** renders the live shader + drag-to-
  rotate works
- **`/api/llm/test` button** successfully validates provider setup
  from the wizard (HTTP path AND the new `mlx://` HF-id probe)
- **Local auto-detect** surfaces running Ollama / LM Studio when
  present
- **Historical Mac desktop CPAL loop** installed and completed the full
  voice round-trip with MLX in-process LLM. The current production
  target is AVAudioEngine voice-processing and still needs the
  signed/notarized DMG + live soak evidence from
  `scripts/validate-macos-native-audio.sh`.
- **MLX-LM adapter** loads Qwen3.5-4B in 1.8s warm, generates at 42
  tok/s decode on M1 base, no thinking-preamble dead air
- **Bench harness** (`python scripts/bench.py --turns 10`) produces
  repeatable per-component numbers — see STATUS.md TL;DR

## What's probable but unverified

These are things I believe work based on code review + unit tests
but have not been validated against a live system. Run through these
before declaring a release.

### QA checklist (still pending)

#### Voice pipeline — beyond the first-hello

- [ ] Multi-turn session (10+ turns) stays stable, no memory leak
- [ ] Session end writes a row to `data/orbis.sqlite` (verify with
  `sqlite3 data/orbis.sqlite "SELECT session_id, ended_at FROM sessions;"`)
- [ ] Second session opens with the `<prior_sessions>` block in the
  system prompt (inspect via Langfuse trace if configured, or add a
  temporary `logger.debug` around `_recall_block`)
- [ ] Soft-neglect kicks in after real days of silence (easiest test:
  hand-edit `ended_at` on a seeded session to `datetime.now - 5 days`
  and observe the orb's mood/nudge on next connect)
- [ ] Personality drift analyzer actually runs + applies deltas
  (look for `[personality] applied N drift delta(s)` in logs or check
  `SELECT * FROM personality_events`)

#### Delegation

- [ ] `delegate_to` with a real A2A target — results stream back
  through the delivery controller + narrate
- [ ] `delegate_to` with an OpenAI-compat target
- [ ] Progress narration during a slow delegation (>5s) kicks in

#### Personality adjustment

- [ ] Say "be more playful" — `adjust_personality` triggers;
  personality_axes table updates

#### Entitlement

- [ ] `/api/config` POST with an `orb` block returns 403 when
  unconfigured + entitlement absent — wait, dev mode is open by
  default so this actually succeeds. To test the gate:
    1. Set `STRIPE_SECRET_KEY + WEBHOOK_SECRET + PRICE_CUSTOMIZATION`
    2. Don't purchase anything
    3. POST `/api/config` with orb block → expect 403
- [ ] Real Stripe test-mode checkout → webhook → entitlement write
- [ ] Cache expiry respects `ENTITLEMENT_CACHE_DAYS` (default 7)

#### Frontend polish

- [x] Setup wizard renders on first boot
- [x] Starter-orb cards render + preview modal works
- [x] Hatch animation timing feels right
- [ ] Profile panel fills in after a few sessions accumulate
- [ ] Voice panel API-key field correctly validates via `/api/whoami`
- [ ] WebGL context-lost warnings (seen during user test) don't
  degrade over a long session

#### Packaging

- [x] Docker image builds (restored post-carve)
- [x] `docker compose up` boots on a GPU host (NVIDIA toolkit present);
  `torch.cuda.is_available()` is True inside the container; Whisper
  loads on `cuda`
- [x] `docker compose -f docker-compose.yml -f docker-compose.cpu.yml up`
  boots on a box without the NVIDIA toolkit; voice still works on CPU
- [ ] `docker compose --profile fish up` activates Fish (unchanged from
  seed; not verified post-carve)
- [ ] First release tag (`v0.1.0`) triggers release.yml cleanly
- [ ] `GH_PAT` set for `prepare-release.yml` auto-bump (was broken
  on protoVoice; status in ORBIS unknown)

## Known issues / rough edges

- **Tool-call translation gap in Ollama + MLX adapters.** Both new
  adapters log a one-time warning and proceed content-only when the
  pipecat context has tool calls — meaning `delegate_to` and
  `adjust_personality` won't reach gemma3+/qwen3+ on Ollama or any
  model on MLX yet. OpenAI-compat path still has full tool support;
  cloud users unaffected. See `voice/llm/ollama.py` + `mlx.py`
  header comments.
- **gemma3n on mlx-lm 0.31.x** has an upstream `sanitize()` bug
  (`KeyError: 'model'`) that breaks loading. Default MLX preset is
  Qwen3.5-4B as a workaround; flip back when the upstream fix lands.
- **MicroAckInjector still fires on every turn.** Trigger lifted
  500ms → 1500ms (PR #30) but with the LLM at ~1s round-trip the
  filler still wins the race. UX call: lift further, drop volume,
  or make conditional on conversation length.
- **Pipecat `STTService._ttfb_timeout_handler` warning** — pipecat
  asyncio bug, cosmetic.
- **`_active_skill()` naming shim.** `app.py` + `a2a/server.py`
  still reference `skill_slug_provider` / `_active_skill()` —
  compat shims returning the Persona. Functional but confusing; due
  for a rename pass.
- **`agent/session_store.py` text-summary redundant with SQLite.**
  Orphan-delivery stash stays; summary file is now dead code.
  Retire in a focused commit once SQLite recall confirmed working
  over a week of use.
- **No per-variant mood visual mapping.** `moodStore` polls + emits
  but no orb variant subscribes. Mood flows into prompts but doesn't
  visually show in the orb — biggest visible gap vs DECISIONS.md.
- **WebGL context-lost warnings** from user test. Two Canvas
  instances (main orb + preview modal) sometimes collide; the main
  orb's context can get reclaimed. Not fatal — variant re-mounts —
  but worth tightening. Options: pause the main stage while preview
  is open, or share a single Canvas via a portal.
- **Docker hostname resolution UX** (task #68). Wizard accepts bare
  hostnames like `ava` that don't resolve inside containers. Inline
  warning + docs note.
- **Stripe refresh is global, not owner-scoped.** Single-owner today;
  needs scoping if ever multi-tenant.
- **Drift analyzer silence-on-error.** If the LLM endpoint is broken,
  personality drift silently never happens. Worth a metrics counter.
- **Frontend CI.** Covered by native-audio preflight and desktop build;
  add broader lint/test coverage only if the frontend grows beyond the
  current setup/build contract.
- **No `docs/` site.** VitePress was purged. README + DECISIONS +
  STATUS + HANDOFF do the work; a rebuilt docs site is future.
- **Select controlled/uncontrolled warning** in console — minor
  shadcn Select component quirk, not blocking.

## Known open questions

1. **Dev-mode customization default — open or closed?** Currently
   open when Stripe is unconfigured so local dev isn't gated. For
   a downloadable install that ships unconfigured, this means
   every un-paid user gets full access. Resolution: env-var toggle
   (`ORBIS_GATE=open|closed`) + document prod installs MUST set
   Stripe before shipping externally.

2. **Per-variant mood mappings — who authors them?** Three options:
   - Code per variant (quick, limiting)
   - JSON alongside each variant's presets (more flexible)
   - Build the authoring editor DECISIONS.md envisioned (biggest
     lift, best product outcome)

3. **Starter orb pool curation.** 8 starters. Right number? Too
   few = not enough personality-match; too many = decision
   paralysis. Worth a usability test.

4. **Stripe price modeling** — currently `mode="payment"`
   (one-time). If the business becomes subscription-based, grant
   events list needs `invoice.paid` + subscription lifecycle
   handling. Code scaffold is there; schema supports it.

5. **Docs site** — rebuild VitePress or something simpler (mkdocs,
   GitHub wiki)? README + HANDOFF + DECISIONS in-repo are probably
   enough until there's a real user-facing audience.

## Recommended next steps (in priority order)

The structure follows the 4-phase plan in `docs/native-audio-direction.md`.
Items in the "Other in-flight" buckets are non-Phase-1 work that's still
worth doing in parallel where it doesn't conflict with the carve.

### Phase 1 — DONE 2026-04-28

All ROI-ranked items shipped except #2 (`webrtc-audio-processing`) and #7 (rubato `FftFixedIn` outside callback). Both deferred — Phase 2's AVAudioEngine adoption supersedes them, integrating webrtc-audio-processing's C++ build dep + writing fresh resampler glue would be thrown-away work in 1-2 weeks. The legacy CPAL path still carries the defensive mic-gain and STT RMS gates; the macOS voice-processing path uses unity gain and waits on live soak before deleting those fallback band-aids. See STATUS.md § "Phase 1 — what shipped" for the per-commit table.

Total Phase-1 delta: **−1,391 net LoC** in the working tree, **−442 kB off the JS bundle** (1,962 → 1,520 kB), and the `unsafe impl Send for AudioEngine {}` shim is gone from `engine.rs`.

### Phase 2 — Apple-native audio

**Phase 2a — DONE 2026-04-28.** `AVAudioEngine` voice-processing input shipped in `src-tauri/src/audio/voice_processing_input.rs`.

**Phase 2 production hardening — IN PROGRESS 2026-05-29.** Production Mac builds now use `native-audio,voice-processing` by default. Build locally with `./scripts/nuke-and-rebuild.sh --launch --tail`; use `--dmg` for an unsigned local installer packaged from the signed `.app`. Validate live on Apple Silicon with `./scripts/validate-macos-native-audio.sh --launch --duration 240` and validate signed releases with `--release --dmg <path>`.

**Phase 2b — pending live validation.** Once the production hardening playbook passes live, the cleanup commit deletes:
- `src-tauri/src/audio/aec.rs` (Apple AEC supersedes)
- The CPAL `build_input_stream` + `preferred_input_config` paths
- `voice/local_transport.py` `MIC_GAIN` + `_apply_gain_i16` (Apple AGC supersedes)
- `voice/stt.py` `STT_MIN_RMS` / `STT_STRONG_RMS` / `STT_MIN_TEXT_LEN` gates
- `app.py` default-off-ing of backchannel + microack
- VAD knob overrides (back to pipecat defaults)

### Phase 3 — protoApp consolidation (Q2)

- **Vendor `protolabs-voice-core`** from `protoLabsAI/protoApp` as a Cargo dep.
- **Migrate Python sidecar to speak `orbis-sidecar`'s WebSocket contract** (`{type, text}` JSON, `ORBIS_READY ws://...` readiness line). See `protoApp/docs/how-to/integrate-orbis-sidecar.md`.
- **Delete `voice/local_transport.py`, `src-tauri/src/audio/socket.rs`, `voice/native_bargein.py`, `voice/sse_bus.py`.**
- **PyApp content-hash cache problem disappears** (sidecar is now `python -m orbis` over WS).

### Phase 4 — iOS (Q3+)

- **Tauri Mobile target** (iOS in alpha as of 2.10.x).
- **Full Rust in-process LLM/STT/TTS** per protoApp's already-shipping pattern: `whisper-rs 0.16`, `kokoros`, `llama-cpp-2` with Metal feature.
- **Python sidecar becomes desktop-only optional** — power-user delegate runtime for agents that need Python deps.

### Other in-flight (do in parallel where it doesn't conflict with the carve)

These were "next steps" in the prior version of HANDOFF.md and remain relevant:

- **Tool-call translation in Ollama + MLX adapters.** Currently warned + skipped — needed for `delegate_to` to reach gemma3+/qwen3+ on local backends. Half-day each.
- **Per-variant mood wiring.** Pick Fractal (default) and wire `useMood()` into its shader uniforms.
- **`_active_skill()` rename pass.** Cosmetic but reduces confusion. 1 hour.
- **Frontend CI.** Native-audio preflight already runs `bun install + bun run build` on PRs and main. Add a separate frontend workflow only if lint/unit coverage grows beyond the current release checks.
- **Retire `session_store.py` text summaries.** Orphan-delivery stash stays; summary file is now dead code.
- **Task #68 — Docker hostname warning** in the LLM wizard step.
- **State/mood authoring editor.** Big UI task; paid customization unlock surface per DECISIONS.md.
- **Live Stripe integration test** — use Stripe test mode end-to-end.

### Longer-term

- **Collectible / shop orbs** (per DECISIONS.md: deferred). Time-limited starter additions, themed drops.
- **Fact extraction background agent.** SQLite facts table is ready; extractor module just needs writing.
- **Observability — Langfuse + Prometheus `/metrics`.** Stubs exist from the seed; wire to the deployment env.
- **Docs site rebuild.** README + DECISIONS + STATUS + HANDOFF + `docs/native-audio-direction.md` carry the load today; if/when there's a real user-facing audience, build a proper docs site (mkdocs / VitePress).

## Useful commands

```bash
# Inspect memory state
sqlite3 data/orbis.sqlite \
  "SELECT session_id, ended_at, length(messages) AS msg_chars FROM sessions ORDER BY ended_at DESC LIMIT 10;"
sqlite3 data/orbis.sqlite \
  "SELECT axis, value, updated_at FROM personality_axes ORDER BY abs(value) DESC;"
sqlite3 data/orbis.sqlite \
  "SELECT subject, relation, object, confidence FROM facts WHERE invalid_at IS NULL ORDER BY confidence DESC;"
sqlite3 data/orbis.sqlite \
  "SELECT axis, delta, reason, at FROM personality_events ORDER BY at DESC LIMIT 20;"

# Force re-run of setup wizard (browser devtools)
localStorage.removeItem('orbis.setupComplete')
localStorage.removeItem('orbis.apiKey')
location.reload()

# Clear memory (full reset)
rm data/orbis.sqlite*

# Test LLM endpoint without going through the pipeline
curl -X POST http://localhost:7866/api/llm/test \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://api.openai.com/v1","model":"gpt-4o-mini","api_key":"sk-..."}'

# Test config round-trip
curl -s localhost:7866/api/config -H "X-API-Key: $ORBIS_KEY" | jq
curl -s -X POST localhost:7866/api/config \
  -H "X-API-Key: $ORBIS_KEY" \
  -H "Content-Type: application/json" \
  -d '{"persona":{"name":"Atlas"}}' | jq

# Trigger personality drift manually (testing the prompt block)
python -c "
from memory import Memory
m = Memory()
m.personality.seed_defaults()
m.personality.drift('playful_serious', 0.5, 'manual test')
m.personality.set_mood(valence=0.4, arousal=-0.2)
print(m.personality.get_mood())
"
```

## Contact

Questions that can't be answered from DECISIONS.md + STATUS.md +
README.md + this file: `git log --format='%an %ae' | sort -u` is the
starting point. Commit messages are deliberately detailed; most
integration decisions are documented in the commit that made them.

Good luck. The spine is solid, the wizard works, voice connects —
the product is ready to iterate.
