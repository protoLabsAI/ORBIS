# ORBIS — Locked Decisions

Frozen snapshot of architecture decisions reached 2026-04-23. This
file is not a design doc — there's no roadmap, phases, or "what to
build first." It's an inventory of what was *decided*. Implementation
details that don't constrain architecture are out of scope here.

Any decision that contradicts this file requires an explicit amendment
(add a `## Amendment — YYYY-MM-DD` section below with the reversal +
reason). Don't silently change direction.

---

## Product

- **Voice-first AI companion.** Realtime bidirectional voice is the
  defining interaction; text/chat is a secondary accessibility mode,
  not the pitch.
- **Router-first capability model.** The orb's primary value is
  delegating to the user's own configured agents (A2A, OpenAI-
  compatible endpoints). ORBIS is not an agent framework in the pile;
  it's a voice frontend for agents the user already has.
- **Differentiated by:** persistent memory, slow-drift personality,
  mood state, soft-neglect behavior, visible personality state. These
  are what make it a companion rather than a voice adapter.
- **Adults only.** Not pitched at minors; safety posture is built for
  25-40yo target demographic.
- **Single-user, single-owner, multi-device.** User hosts their own
  instance on tailnet; phone + PC + whatever all hit the same
  instance. No multi-tenant isolation.

---

## Architecture

### Voice pipeline
- **Pipecat stays as the sidecar orchestrator.** STT, TTS, VAD, and
  voice-quality controllers (filler, backchannel, barge-in, micro-ack,
  echo-guard, prosody, delivery) are retained where they fit the native
  desktop product. WebRTC/browser transport was explicitly removed by
  the 2026-04-28 amendment and remains rejected by the 2026-05-29 fork
  posture below.

### TTS providers
- **kokoro default.** CPU-friendly, runs on broader hardware, no GPU
  dependency, no Fish server needed.
- **Optional alternates:** ElevenLabs, OpenAI-compatible TTS URL
  (user-configurable). BYO keys.
- **Voice cloning:** dropped entirely. `/api/voice/clone` and related
  endpoints deleted. Users who want custom voices bring their own
  Fish or clone service and point the OpenAI-compat URL at it.
- **Fish TTS:** retained as an opt-in path (not default, not in default
  image). No `Dockerfile.fish` in default build.

### LLM
- **Small/fast LLM for the voice agent itself.** Qwen-tier or similar —
  the voice brain is a router + personality layer, not a heavy
  reasoner.
- **Heavy reasoning via `delegate_to`**, not in-process. No bundled
  protoAgent. No in-process LangGraph. Smart agents are external
  delegate targets.

### Auth
- **Single-owner primitive kept.** API key verification stays so a
  tailnet neighbor can't use your orb. But the multi-tenant machinery
  (user roster, `allowed_skills`, admin/user roles, `pinned_viz`)
  is deleted.

### Skills
- **Skills system deleted entirely.** One orb persona per install.
  The `skills/` Python package, `config/skills/*.yaml` catalog, the
  YAML-inheritance loader, per-skill prompt/voice/tools overrides —
  all gone.
- Persona and voice **are user-configurable** but via a single config
  file, not a catalog of interchangeable personas.

---

## Tool surface

The ORBIS voice agent has a deliberately small tool surface. Heavy
capability comes through delegation.

### Primary
- `delegate_to(target, query)` — the spine. Existing A2A + OpenAI-
  compatible delegate infrastructure retained unchanged.

### Personality adjustment
- `adjust_personality(axis, delta)` — explicit user-directed personality shifts

Orb visual control (variant, palette, params, presets) is handled outside the LLM tool surface.

### User-interaction primitives (optional — add if useful)
- `remember(fact)` — explicit commit to long-term memory
- `show_inbox(text)` — push to chat without speaking aloud
- `confirm(prompt)` — pause voice, wait for yes/no

### Deleted from the seed
- `calculator`, `get_datetime`, `web_search`, `fetch_url`,
  `slow_research`, `a2a_dispatch` — all become user-configured
  delegates if the user actually needs them.

---

## Memory

- **SQLite, single-file embedded.** No graph DB service, no Neo4j,
  no Postgres, no vector DB daemon.
- **Tables (shape, not schema):**
  - `sessions` — one row per voice session, atomically persisted at
    session end
  - `facts` — with `valid_at`, `invalid_at`, `confidence`, source
    episode reference (Graphiti-shaped but SQL-native)
  - `personality_axes` — one row per axis, drift value + timestamp
  - `personality_events` — time-series of drift events (optional)
  - `mood` — short-term emotional state
  - `entitlement_cache` — local mirror of Stripe verification
- **FTS5** for text retrieval on sessions and facts.
- **`sqlite-vec`** for semantic search — optional, not day one, added
  only if FTS5 + BM25 proves insufficient.
- **Curator** applies 90-day confidence half-life decay on facts
  (protoAgent pattern, already in-tree). Prunes below ~0.2 confidence.
- **Optional background entity-extractor agent** does LLM-driven fact
  mining from raw episodes. Opt-in, not hot-path, not day one.
- **Per-user scope only.** No per-skill keying (skills are gone). Just
  `(user_id, *)`.

---

## Personality

- **Many axes, Seaman-flavored.** Not 3-4; many. Spanning mood
  (warm/guarded, playful/serious, hopeful/cynical), rhetorical style
  (sarcastic/sincere, verbose/terse, grandiose/grounded), curiosity
  shape (probing/incurious, philosophical/pragmatic), neediness
  (independent/clingy), etc. **Exact set chosen at implementation.**
- **Drift: both directable and automatic.**
  - Directable: user says "be more playful" — sticks.
  - Automatic: axes drift from interaction patterns over weeks.
  - Neither dominates; both compose.
- **Mood state:** shorter-term than personality. Visible in orb
  visuals (slow cool pulse vs rapid warm turbulence vs desaturation).
- **Soft-neglect kicks in over days.**
  - Day 2-3 of silence: mood visibly shifts
  - Day 4-7: noticeable guardedness
  - Return: "relieved to see you" warmup
  - **No death.** No mortality stakes. Adult product; grief mechanics
    are for teen-oriented pet games.
- **Visible personality state:**
  - Implicit (primary): voice + behavior + orb visuals reflect the
    current state.
  - Explicit (secondary): Profile panel in the drawer surfaces mood +
    axes + recent memory highlights for users who want to peek.

---

## Visualization

- **Existing orb stays.** R3F + variant registry (Fractal, Nebula,
  Crystal, Particles) + shared driver hooks + broadcast bus +
  localStorage preset store. All inherited from protoVoice.
- **Starter orb acquisition:** user picks one of N from a curated
  pool shipped with the binary. Not random — user chooses. Starter
  pool definition is implementation detail.
- **Self-modification via conversation.** Orb appearance changes are
  driven by external process signals, not LLM tool calls.
- **Paid unlock:** full editing of all variants + all palettes +
  per-param tweaking behind a one-time purchase.

---

## Monetization

- **Free tier:** one starter orb (picked from the curated pool),
  full companion experience (memory, personality, voice, delegation).
  A complete product on its own.
- **Paid tier:** one-time Stripe purchase unlocks full customization
  (all variants, all palettes, per-param editing) + whatever future
  shop items get added.
- **Entitlement model:** phone-home verification against Stripe + local
  N-day cache for offline tolerance. User tells us the cache window;
  default TBD.
- **Explicitly not doing:** gacha, loot boxes, energy timers,
  pay-to-progress, game-mechanic collectibles, cosmetic FOMO cycles,
  season passes, subscriptions (for v1; revisit later if needed).
- **Future:** shop for special/seasonal orbs. Deferred.

---

## Configuration

- **Format:** YAML.
- **Shape:** one main config file in the repo tree (`config/orbis.yaml`
  or similar). Single source of truth.
- **UI mirror:** all user-editable settings exposed in the drawer /
  settings UI. UI reads from and writes back to the file. Reload-on-
  write on the server side.

---

## First-run experience

- **Setup wizard (UI).** Runs on first boot. Flow TBD but shape is:
  set auth key → pick starter orb from N → configure TTS provider →
  add first delegate (A2A or OpenAI-compat) → hatch.
- **Hatch animation.** One-time, unique to the installation, orb
  forms and speaks first words. Specific animation TBD.

---

## Deleted from the protoVoice seed

Concrete carve list (enforced by subsequent commits):

- `skills/` Python package
- `config/skills/*.yaml` catalog
- `config/SOUL.md` as a skill-system dependency
- Multi-tenant parts of `auth/users.py` (`allowed_skills`, role split,
  pinned_viz, full roster)
- `/api/whoami`, `/api/users/reload`, `/api/admin/*`, `/api/skills/*`,
  `/api/voice/clone`, `/api/voice/references`
- `web/src/auth/` (whoami store + role-gating hooks)
- `SkillSelector.tsx`, admin tab gating, lock-chip branches
- `Dockerfile.fish` in the default build path
- Fish service in `docker-compose.yml` default profile
- Most of the v0.12.1 test suite (users, endpoint admin variants,
  allowed_skills cases)
- `pyproject.toml` heavy deps that the voice stack doesn't actually
  need (vllm, torch for anything other than local audio model,
  transformers unless Whisper is local, etc. — case by case)

## Deferred / not blocking

- Exact personality axis set (many, Seaman-flavored — at implementation)
- Soft-neglect exact thresholds (days — tuned at implementation)
- Starter orb pool (N count, specific variants + palettes)
- Setup wizard UI details
- Hatch animation design
- Stripe integration specifics (cache TTL, webhook exact shape)
- Pluggable-TTS layer wire-up specifics

## Amendment — 2026-04-23: orb state + mood authoring

The orb's visual reacts to both voice state and mood. Authoring those
reactions needs to be a first-class editor surface.

- **Voice states** are `idle`, `listening`, `thinking`, `speaking`.
  "Breathing" is not a separate state — it's the ambient animation
  layer present in all four, with per-state intensity.
- **Mood dimensions** are `valence`, `arousal`, `guardedness` (see
  memory/personality.py). Each is in [-1, +1] and drives shader
  uniform deltas.
- **Preset shape is deltas, not absolutes.** A preset stores
  `(variant, palette, base_params)` plus `state_overrides` (per voice
  state) and `mood_overrides` (per mood dim) as deltas applied on
  top of base. This composes cleanly — a speaking+cynical orb is
  base + speaking delta + cynical delta, not a separately-authored
  (speaking, cynical) cell.
- **Editor gating:** the full state/mood authoring editor is part of
  the paid customization unlock. Free tier runs a starter preset
  that's pre-authored; free users don't get the authoring tool.
- **Config file stays one.** `config/orbis.yaml` grows to hold
  `state_overrides` + `mood_overrides` per preset rather than
  adding a second file.

Task impact: task #53 (mood + visual reflection) is now the
reflection engine *plus* the authoring editor. Task #55 (config UI
mirror) now has more surface to mirror. Task #59 (hatch animation)
benefits from the state-authoring tooling because hatch is a
state-transition timeline.

## Amendment — 2026-04-23: docker default is GPU-first

The original "no GPU dependency" statement in § TTS providers is still
true for kokoro itself — it runs fine on CPU. But the default docker
path now reserves an NVIDIA GPU for the orbis service so Whisper STT
and Kokoro both run on CUDA. Reason: CPU Whisper is multi-second per
utterance — the single biggest latency source in a turn. Voice-first
as a product promise is unreachable without that acceleration.

- **Default:** `docker compose up` requires an NVIDIA GPU + driver ≥
  570 + `nvidia-container-toolkit`. Torch is pinned to a `+cu128`
  wheel in the Dockerfile so it matches the container's CUDA 12.8
  base image.
- **CPU-only override:** `docker-compose.cpu.yml` strips the GPU
  reservation (`!reset []` on the device list) and swaps `runtime`
  back to `runc`. Users layer it with `-f`:
  `docker compose -f docker-compose.yml -f docker-compose.cpu.yml up`.
  The app still works, it's just slower.
- **Native `python app.py`** is unchanged and remains fully CPU-
  viable — no toolkit requirement, no override file. The GPU-first
  posture is strictly a docker concern.

## Amendment — 2026-04-24: LLM factory + MLX-LM as Apple-Silicon default

The LLM has graduated from "single OpenAILLMService talking to whatever
URL is configured" to a small adapter pattern under `voice/llm/`:

  voice/llm/__init__.py     — make_llm() factory + provider auto-detect
  voice/llm/openai.py       — re-export of pipecat's OpenAI-compat path
  voice/llm/ollama.py       — native /api/chat (so `think: false` works)
  voice/llm/mlx.py          — Apple Silicon native via mlx-lm

Selection precedence (in `make_llm`):

  1. Explicit `provider="..."` kwarg
  2. `mlx://<huggingface-id>` URL scheme  → MLXLLMService
  3. URL shape (port 11434, "ollama" hostname) → OllamaLLMService
  4. Probe `<root>/api/version` 200 → OllamaLLMService
  5. Fall back to OpenAILLMService

Why each adapter exists:

- **Ollama-native** — Ollama's OpenAI-compat /v1/chat/completions
  silently ignores the `think: false` request field. Models with
  reasoning preambles (gemma3/4, qwen3, deepseek-r1) jam pipecat's
  sentence aggregator until the reasoning phase ends. Native
  /api/chat honors `think`; first content tokens land in 100-300ms
  instead of 6-8s.
- **MLX-LM** — Mac users no longer need a separate Ollama install.
  Models download into the HF cache the same way Whisper and Kokoro
  already do; the LLM runs in-process inside the Python sidecar.
  ~2× faster than llama.cpp on Apple Silicon for the same
  quantization. Lazy-imported so non-Mac builds keep working without
  the dependency.

Default desktop wizard preset is now `mlx-community/Qwen3.5-4B-MLX-4bit`.
The OllamaInstallHelper preset stays available for users who already
run Ollama or want to share models with other tooling. We deliberately
don't auto-upgrade Ollama users to MLX — a multi-GB silent download
under the user violates the "no surprises" principle.

This decision originally pushed an Apple Silicon desktop focus. As of the
2026-05-29 amendment below, that means Mac first for production hardening;
Linux and Windows desktop builds are deferred until the Mac native-audio path
is proven, not rejected.

## Amendment — 2026-04-24: Tauri shell + WebContent media capture

> Historical note: this WebContent media-capture patch set was superseded by
> the 2026-04-28 web/PWA removal and the 2026-05-29 native Mac audio hardening
> path. The active production path uses Rust-owned microphone permission and
> native audio; it does not use WebKit `getUserMedia`.

The Tauri 2 desktop shell shipped with three runtime patches that made the
WKWebView's WebContent subprocess usable for real-time voice on a
Developer-ID-signed Mac build:

- `src-tauri/src/mic_permission.m` — calls
  `AVCaptureDevice.requestAccessForMediaType:` at app boot so TCC
  registers our bundle id. Without this, our app never appears in
  System Settings → Privacy & Security → Microphone.
- `src-tauri/src/media_permission_patch.m` — runtime swap of wry's
  WKUIDelegate. wry hardcodes `WKPermissionDecision::Grant` for
  media capture, which bypasses TCC and hands WebContent a dead
  audio stream. We replace the decision with `Prompt`, which routes
  through TCC properly. Re-applies on a 1-second heartbeat so
  reload / new-webview events get caught.
- `src-tauri/entitlements.plist` — previously added audio input, camera,
  network, JIT, and library-validation exceptions required by hardened runtime
  + WebContent. Current production entitlements are microphone audio input,
  network client/server, and the narrow WKWebView JIT exception; camera and
  broad code-signing exceptions are intentionally absent.

These patches are Mac-specific (no-op on other platforms via cfg).
They're considered part of the supported architecture, not
workarounds — wry's media-capture default isn't going to change
upstream anytime soon, and the Apple-side requirements are stable.

---

## Amendment — 2026-04-27: Dual-transport audio architecture

**Supersedes:** "Pipecat stays as-is. WebRTC, STT, TTS, VAD, voice-quality controllers — all kept."

**What changed:** The transport layer is no longer always WebRTC. On desktop (Tauri), audio is routed through native CPAL (CoreAudio on macOS) via a Rust sidecar — no WebRTC session is opened. WebRTC remains the first-class path for browser/PWA clients.

**Decision:** Support two first-class audio transports in the same codebase, selected at startup via `AUDIO_TRANSPORT=native|webrtc`. Both feed the same shared Pipecat pipeline session.

```
AUDIO_TRANSPORT=native  →  CPAL mic → Unix socket → LocalAudioTransport
                         → MultiInputMixer → shared pipeline
                         → TeeFrameProcessor → LocalAudioOutputSink (CPAL speakers)
                                             → WebRTCOutputSink (if a WebRTC client joins)

AUDIO_TRANSPORT=webrtc  →  getUserMedia → SmallWebRTCTransport (unchanged)
```

**Why:**
- WebRTC in a Tauri WKWebView requires two Obj-C shims, TCC registration, and hardened-runtime entitlements. Even then, the audio stack is mediated by the browser engine, limiting AEC and device control.
- CPAL gives direct CoreAudio access: device selection, sample-rate negotiation, native AEC via speexdsp, lower latency (~20ms less round-trip).

**Historical architecture toggle:** `AUDIO_TRANSPORT=native|webrtc` selected the
transport during this brief dual-transport phase. This was superseded on
2026-05-29 by the Mac-first native-only desktop path: production desktop builds
enable `native-audio,voice-processing`, Tauri spawns the sidecar with
`AUDIO_TRANSPORT=native`, and omitting `native-audio` is not a supported
desktop product build.

**SSE state bridge (Phase 5):** In native mode, no RTVI data channel exists, so the frontend reads voice state from a new SSE endpoint (`GET /api/events`) that the `SseBusObserver` pipeline observer publishes to. `VoiceStateBridge.tsx` detects native mode via `/healthz` on mount and activates the `useNativeBridge` hook instead of relying on RTVI events.

**New modules:**
- `voice/transport_factory.py` — `make_transport()`; `AUDIO_TRANSPORT` constant
- `voice/local_transport.py` — `LocalAudioInputTransport` / `LocalAudioOutputTransport`
- `voice/sse_bus.py` — `SseBus`; `/api/events` fan-out
- `voice/native_bargein.py` — `NativeBargeInObserver`
- `voice/tee_processor.py` — `TeeFrameProcessor`, output sinks
- `voice/multi_input_mixer.py` — `MultiInputMixer`
- `web/src/voice/useNativeBridge.ts` — SSE subscriber hook

**Orb-control tools removed (same session):** `set_variant`, `apply_palette`, `adjust_param`, `save_preset`, `recall_preset` removed from the LLM tool surface entirely. Future orb state changes come from external process signals, not LLM function calls. See `DECISIONS.md` — removed tools section above.

---

## Amendment — 2026-04-28: Apple Silicon (+ iOS planned) first; drop web/PWA

> Platform-scope note: the 2026-05-29 amendment below supersedes the
> "Apple Silicon only" framing. The enduring decision here is dropping the
> web/PWA/browser runtime and moving desktop audio to native transport.

**Supersedes:** "Dual-transport audio architecture" (2026-04-27 amendment above) and the "WebRTC remains the first-class browser/PWA path" framing carried from the original architecture section.

**What changed:** Web / PWA / browser is dropped entirely as a supported runtime. ORBIS targets **Apple Silicon Mac** as the first production desktop platform for this hardening pass. **iOS / iPad** is the planned secondary target. The dual-transport `AUDIO_TRANSPORT=native|webrtc` toggle goes away — there is one transport, and it is native CPAL today, AVAudioEngine voice-processing IO in Phase 2, `protolabs-voice-core` (vendored from `protoLabsAI/protoApp`) in Phase 3, and the same on iOS in Phase 4.

**Why (research-validated 2026-04-28, three parallel streams):**
- The cross-platform reach the WebRTC path provided is currently zero benefit. Linux/Windows desktop is already deprioritized (Docker self-host); a browser path adds maintenance cost and zero strategic value.
- Apple Silicon's `AVAudioEngine` voice-processing IO ships AEC + AGC + NS tuned per-Mac-model (WWDC23-10235). Today's debug session spent the entire day band-aiding the absence of those — software mic gain, custom AEC, hand-tuned VAD thresholds, hallucination filters. Apple solves all of it.
- `protoLabsAI/protoApp` already ships the in-process Rust voice substrate (`whisper-rs` + `kokoros` + `llama-cpp-2` behind feature flags) and the WebSocket sidecar contract (`orbis-sidecar`). Migrating ORBIS to that pattern eliminates ~1,432 lines of bespoke audio plumbing, and is a prerequisite for the iOS port (no Python on iOS).
- The only public Tauri+Pipecat reference (`kstonekuan/tambourine-voice`) and Daily/Pipecat's own desktop demo (`kwindla/macos-local-voice-agents`) both ship audio over WebRTC-to-localhost. We made the opposite call for latency / direct CoreAudio access. With this amendment, we double down on that decision and replace the browser-WebRTC fallback with a clean iOS path.

**Decision (4-phase plan; full detail in [`docs/native-audio-direction.md`](./docs/native-audio-direction.md)):**

1. **Phase 1 — Strip web** (this week). Delete WebRTC client deps, PWA service worker, `getUserMedia` paths, `voice/multi_input_mixer.py`, `voice/transport_factory.py` factory branching, `/api/offer`, `media_permission_patch.m`, the `audioTransport === 'webrtc'` branches in the React app. Roughly 600+ LoC out + several MB off the JS bundle.
2. **Phase 2 — Apple-native audio** (1–2 weeks). Replace CPAL input + custom `aec.rs` with `AVAudioEngine` voice-processing IO via `objc2-avf-audio`. Re-enable backchannel + microack now that real AEC is in place. Delete the 8× software-mic-gain hack and the `STT_MIN_RMS` / `STT_STRONG_RMS` gates we added today.
3. **Phase 3 — protoApp consolidation** (Q2). Adopt `protolabs-voice-core` from `protoLabsAI/protoApp` as the shared Rust audio + inference substrate. Migrate the Python sidecar to speak `orbis-sidecar`'s WebSocket contract instead of our 8-byte-header binary Unix socket.
4. **Phase 4 — iOS** (Q3+). Full migration to in-process Rust per protoApp's already-shipping pattern. Tauri Mobile target. Python sidecar becomes desktop-only optional.

**Trade-offs explicitly accepted:**
- No browser / PWA access. Users wanting ORBIS from a phone-not-on-tailnet browser are out of luck (mitigation: iOS app is on the roadmap; tailnet remains the multi-device answer for desktop).
- No Linux/Windows desktop in the Mac hardening pass (mitigation: Docker self-host stays documented until the native desktop ports are added).
- Tighter coupling to Apple's audio stack (mitigation: AVAudioEngine has been stable since 10.10; voice-processing IO since 10.13).
- Migration in three phases is non-trivial; we accept this in exchange for permanent simplification.

**Modules deleted in Phase 1** (does not bind Phase 2+):
- `voice/multi_input_mixer.py` — only existed to arbitrate CPAL + WebRTC mics
- `voice/transport_factory.py` — factory always returned `LocalAudioTransport` after this
- `/api/offer` endpoint — WebRTC signalling only
- `src-tauri/src/media_permission_patch.m` — `getUserMedia` UIDelegate hack, dead without WebRTC
- `web/src/shared/audio/MicTest.tsx`, `recordWav.ts` — `getUserMedia` paths
- `vite-plugin-pwa` plugin (run with `selfDestroying: true` for one release first)
- WebRTC dual-flush branch in `voice/native_bargein.py`
- `AUDIO_TRANSPORT` env var (implicit `native`)

**Modules deleted in Phase 3** (does bind Phase 2):
- `voice/local_transport.py` (replaced by `orbis-sidecar` WS contract)
- `src-tauri/src/audio/socket.rs` (replaced by WS framing)
- `voice/sse_bus.py` (replaced by WS event stream)
- `voice/native_bargein.py` (functionality moves to protoApp host)

**See:** [`docs/native-audio-direction.md`](./docs/native-audio-direction.md) for the comprehensive guide — full file/feature delete inventory, ROI-ranked Phase 1 actions list (11 items synthesized from the three research streams), the protoApp migration target, and citations for every claim.

---

## Amendment — 2026-05-29: Mac-first desktop, Linux/Windows later

**Supersedes only the platform-scope wording in the 2026-04-28 amendment.**
The web/PWA/browser rejection still stands. Native audio remains the desktop
transport direction.

**What changed:** ORBIS is no longer framed as Apple Silicon only forever.
The production desktop sequence is now:

1. Harden the Apple Silicon Mac build first with native
   `native-audio,voice-processing`, Developer ID signing, notarized DMG,
   microphone-only entitlements, and live AVAudioEngine validation.
2. Add Linux and Windows desktop builds after the Mac path is stable, using
   the native transport shape rather than restoring browser WebRTC.
3. Keep iOS / iPad as the planned secondary Apple target.

**Why:** The Mac build is still the fastest path to a production-quality native
audio experience because AVAudioEngine voice-processing gives us AEC + AGC +
noise suppression now. Linux and Windows support should follow from that
hardened native architecture, but they should not hold the Mac release path
open or reintroduce the removed web/PWA runtime.

**Decision impact:** CI and release automation stay Mac-only until the Mac DMG
passes signed/notarized release validation and live microphone soak. Docs and
future planning should say "Mac first" instead of "Apple Silicon only" when
describing product scope.

**Fork policy:** `protoLabsAI/orbis-native` is a greenfield Tauri-first fork,
not a temporary divergence from upstream `protoLabsAI/ORBIS`. Upstream remains
useful as a source of narrow ideas: provider settings, delegation, memory,
observability, backend correctness, and desktop UX can be cherry-picked when
they fit the native product. Hosted SPA reach, browser audio, PWA
installability, cross-device web pairing, generated clients tied to that
hosted shape, and any deletion of the native shell or audio stack are out of
scope unless explicitly redesigned for Tauri IPC and the local sidecar.

---

## Amendment — 2026-05-30: Keep torch-MPS KPipeline for Kokoro; reject kokoro-onnx swap

**Context:** `orbis-3kw` proposed swapping our in-process Kokoro TTS
(`voice/tts/kokoro.py`, the `kokoro`/`KPipeline` PyTorch package on MPS) to
Pipecat's official `KokoroTTSService`, which is built on `kokoro-onnx`
(ONNX Runtime), on the assumption that ONNX / CoreML is faster on Apple Silicon.

**Measured on the M1 Pro dev machine** (af_heart, two conversational sentences,
streaming first-chunk TTFA + wall/audio RTF, warm):

| Path | TTFA | RTF | Model |
| --- | --- | --- | --- |
| **torch KPipeline (MPS)** — current | **574 ms** | **0.137** | base dep |
| onnx fp16 CPU (best onnx config) | 780 ms | 0.209 | 169 MB |
| onnx fp32 CPU | 946 ms | 0.257 | 310 MB |
| onnx fp16 CoreML | 931 ms | 0.249 | 169 MB |
| onnx fp32 CoreML | 1088 ms | 0.294 | 310 MB |
| onnx int8 CPU | 2330 ms | 0.637 | 88 MB |
| onnx int8 CoreML | 2517 ms | 0.685 | 88 MB |

**Decision: do not swap.** The torch-MPS path we already ship beats every
kokoro-onnx configuration on both time-to-first-audio and throughput. The best
ONNX config is ~1.4× slower TTFA; int8 (the only small model) is ~4× slower
(quantized ops aren't accelerated on Apple); CoreML EP is slower than plain CPU
because only ~1060/2476 graph nodes map to CoreML and the partition transfers
cost more than they save. A swap would also add a 169–310 MB model file plus
`onnxruntime` and an espeak-ng phonemizer to the bundle — net regression on our
only first-class platform.

**Implication:** "adopt Pipecat's official service" is not automatically the
right call when Pipecat's service wraps a backend that is slower on our target
hardware. We keep `LocalKokoroTTS` (torch/MPS, misaki g2p — no espeak dep).
kokoro-onnx is **not** bundled. Revisit only if (a) a materially faster Apple
path appears (e.g. an MLX Kokoro port that beats MPS), or (b) we add a non-Apple
desktop target where torch-MPS is unavailable and ONNX-CPU is the floor.

---

## Explicitly out of scope

These were considered and rejected during the design conversation:

- Bundled/vendored protoAgent (rejected in favor of pure delegation)
- OpenAI Realtime API as the voice stack (rejected — economics + vendor lock)
- Collectible orb economy with rarity tiers (rejected — wrong game)
- Idle-game progression (Resonance, Attunement, Rebirth) (rejected — wrong game)
- Sanctum-as-visitable-space, asynchronous social (rejected — out of product scope)
- Multi-tenant roster with per-user `allowed_skills` (rejected — we're single-user)
- Skills-as-personas catalog (rejected — one orb per install)
- Fish TTS as default (rejected — broader hardware support via kokoro)
- kokoro-onnx / Pipecat `KokoroTTSService` as the Kokoro backend (rejected
  2026-05-30 — measured ~1.4–4× slower than torch-MPS on Apple Silicon; see
  amendment above)
- Web / PWA / browser as a supported runtime (rejected 2026-04-28)
- Linux / Windows desktop builds in the Mac hardening pass (deferred 2026-05-29 until the Mac native-audio release path is proven)
