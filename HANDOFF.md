# HANDOFF — ORBIS

*Updated 2026-04-28 (after the architectural redirect: Apple-Silicon-only,
drop web/PWA target). The 2026-04-24 desktop-voice arc still applies as
the substrate; what changed is direction, not what's shipped.*

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
is the comprehensive guide for the Apple-Silicon-only direction and
the 4-phase migration plan that supersedes the dual-transport
architecture.

## 2026-04-28 update — direction change

**ORBIS is now Apple-Silicon-only on the desktop, with iOS / iPad as
the planned secondary target. Web / PWA / browser is dropped as a
supported runtime.** This carves out roughly 600+ LoC across
`voice/multi_input_mixer.py`, `voice/transport_factory.py`,
`/api/offer`, `media_permission_patch.m`, the WebRTC client deps in
`web/package.json`, and the `audioTransport === 'webrtc'` branches
throughout the React app. See DECISIONS.md amendment of the same
date and `docs/native-audio-direction.md` for the comprehensive guide.

Today's debug session (Pipecat 1.0 → 1.1 broke `SseBusObserver`'s
`TaskObserver` protocol; voice loop dead in the morning, working by
afternoon) produced a half-dozen Phase-1-tier fixes that are
band-aids — they make the loop functional today; the proper fix is
the migration plan. STATUS.md § "Today's working-tree changes"
inventories what's uncommitted.

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
  **Mac desktop build runs the full voice loop end-to-end** —
  signed + notarized .dmg installs, mic permission prompts cleanly,
  audio reaches Pipecat, MLX-LM in-process replies, Kokoro speaks
  back. ~1.0-1.2s first-audio-out per turn on M1 base. Remaining
  work is polish + follow-ups.

## What works — verified live

- `python -m pytest` → 131 passing, zero failures
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
- **Mac desktop build (signed + notarized .dmg)** installs from the
  CI artifact, opens, prompts for mic permission via TCC, completes
  the full voice round-trip with MLX in-process LLM
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
- **Frontend CI.** No `bun run build` in the release pipeline.
  Add `.github/workflows/frontend-check.yml` on next pass.
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

### Phase 1 — strip web (this week, in progress)

Ranked by ROI per the 2026-04-28 research synthesis:

1. **Replace `~/Library/WebKit/<bid>` shell-rm with `Webview::clear_all_browsing_data()`** (Tauri 2.0 Rust API). Half-day. Eliminates the entire "Load failed after rebuild" class of bug.
2. **Drop `aec.rs` (187 LoC) → adopt `webrtc-audio-processing 2.0.4`.** Weekend, net **−70 LoC**. Real AEC + AGC + NS + VAD; eliminates today's 8× software-mic-gain hack and `STT_MIN_RMS` gates. **This is interim** — Phase 2 supersedes with AVAudioEngine.
3. **Bump `cpal 0.15.3 → 0.17.3`.** Weekend, ~50 LoC. Drops `unsafe impl Send for AudioEngine`; exposes `ErrorKind::DeviceChanged` for AirPods hot-swap.
4. **Ad-hoc sign every dev build with stable `--identifier studio.protolabs.orbis`.** 1 day. Add `codesign --force --deep --sign - --identifier studio.protolabs.orbis ...` to `beforeBundleCommand`. Kills the two-bundle-ID drift AND stabilizes TCC across rebuilds.
5. **Adopt `tauri-plugin-log 2.8.0`.** 1–2 days. Tee `CommandEvent::Stdout/Stderr` via `log::info!(target:"sidecar", ...)`. Unify Rust + frontend + sidecar stdio into one rotating log file.
6. **`SseBusObserver` → subclass `RTVIObserver`.** Half-day. Stops forking the RTVI event vocabulary; future `pipecat-client-react` adoption becomes drop-in.
7. **Move `rubato::resample_linear` out of audio callback → `FftFixedIn`** built once outside (mirrors `cjpais/Handy`). Half-day, ~30 LoC.
8. **`selfDestroying: true` on `vite-plugin-pwa`** (transition release), then remove the plugin entirely.
9. **`enable_rtvi=False` on `PipelineTask`.** 1 line. Silences the boot warning since we already construct both manually.
10. **CASTER 20-channel broadcast bug fix** (output callback writes mono to all 20 channels). <1 hour, 5 LoC. Becomes irrelevant after Phase 2 if output also moves to AVAudioEngine.
11. **Delete `voice/multi_input_mixer.py`** (170 LoC) — only existed for CPAL+WebRTC arbitration.
12. **Delete WebRTC client deps from `web/package.json`** + WebRTC branches in `OrbStage.tsx`/`VoiceStateBridge.tsx`.
13. **Delete `/api/offer`, `media_permission_patch.m`, `voice/transport_factory.py` factory branching, `MicTest.tsx`, `recordWav.ts`.**

### Phase 2 — Apple-native audio (1–2 weeks after Phase 1 lands)

- **Migrate input from CPAL → `AVAudioEngine` voice-processing IO** via `objc2-avf-audio` (per WWDC23-10235). Apple ships AEC + NS + AGC tuned per Mac model.
- **Delete `aec.rs` entirely.** Apple does it now.
- **Delete the `MIC_GAIN`, `STT_MIN_RMS`, `STT_STRONG_RMS`, `STT_MIN_TEXT_LEN` knobs.** Today's band-aids dissolve.
- **Re-enable backchannel + microack** in native mode (currently `default off`). Real AEC means no false-trigger on bot tail.
- **VAD back to defaults** (`confidence=0.7, min_volume=0.6`).

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
- **Frontend CI.** Add `.github/workflows/frontend-check.yml` that runs `bun install + bun run build` on every PR.
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
