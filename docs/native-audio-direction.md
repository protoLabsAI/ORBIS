# Native audio direction — 2026-04-28

> 2026-05-29 update: platform scope is now Mac-first, not Mac-only
> forever. Apple Silicon Mac is the current production desktop target while
> signing, notarization, microphone permission, and AVAudioEngine
> voice-processing are hardened. Linux and Windows desktop support are
> sequenced after the Mac release path is stable. Web / PWA / browser remains
> dropped.

Comprehensive decision guide for ORBIS's audio + transport architecture
following the 2026-04-28 debug session and three parallel research
streams.

This document is the **source of truth** for the architectural
direction. STATUS.md, HANDOFF.md, and DECISIONS.md amendments all
reference it. If you change direction here, update those.

---

## TL;DR

ORBIS targets **Apple Silicon Mac** as the first production desktop platform.
**Linux / Windows desktop** support follows after the Mac native-audio release
path is stable. **iOS / iPad** remains the planned secondary Apple target.
**Web / PWA / browser** is dropped entirely as a supported runtime.

The architecture migrates in four phases:

1. **Strip web** (this week) — delete WebRTC client, PWA service worker,
   getUserMedia paths, multi-input mixer, transport factory branching.
   Net ~600+ LoC removed plus several MB of bundle weight.
2. **Apple-native audio** (Mac hardening) — replace CPAL input + custom AEC
   with AVAudioEngine voice-processing IO via `objc2-avf-audio`. Apple
   ships AEC + AGC + NS tuned per Mac model. The current Mac path defaults
   microphone gain to unity; the legacy CPAL defensive gain and STT RMS gates
   stay only until live soak proves the voice-processing path.
3. **protoApp consolidation** (Q2, weeks) — adopt `protolabs-voice-core`
   from `github.com/protoLabsAI/protoApp` as the shared Rust audio +
   inference substrate. ORBIS becomes a Python sidecar speaking the
   `orbis-sidecar` WebSocket contract instead of a hand-rolled binary
   Unix socket.
4. **iOS** (Q3+) — full migration to in-process Rust (`whisper-rs`,
   `kokoros`, `llama-cpp-2`) per protoApp's already-shipping pattern.
   Python sidecar becomes desktop-only optional.

---

## Why this direction

Three research streams (see references at end) reached consensus on
the following:

- ORBIS is **genuinely off the paved road**. The only public Tauri +
  Pipecat reference (`kstonekuan/tambourine-voice`) and Daily/Pipecat's
  own desktop demo (`kwindla/macos-local-voice-agents`) both ship audio
  over **WebRTC to localhost**, not Unix-socket PCM. ORBIS made the
  opposite call to get lower latency and direct CoreAudio access. That
  choice is defensible — but it cost us ~1,432 lines of bespoke audio
  plumbing, a custom AEC that's worse than what's on crates.io, and an
  entire day of debugging mic gain / VAD / hallucination filters.
- The cost of browser-style cross-platform reach (Web/PWA via WebRTC)
  is currently zero benefit — WebRTC has been a source of complexity,
  not portability. Linux and Windows desktop support should use the native
  transport shape after Mac stabilizes, not resurrect the browser runtime. The
  browser path's mic permission UIDelegate hack alone
  (`media_permission_patch.m`) was a 50-line Obj-C runtime swap.
- Apple Silicon **already gives us a better answer** for AEC + AGC + NS
  than anything in the Rust audio ecosystem: `AVAudioEngine` voice-
  processing IO, per WWDC23-10235. It's tuned per-Mac-model by Apple's
  acoustic engineers. Using it solves the problems we spent today
  band-aiding (M1 internal mic too quiet, custom AEC too weak, echo
  bleed false-triggering VAD).
- protoApp ships the in-process Rust voice substrate already. Using
  `protolabs-voice-core` instead of duplicating audio code removes the
  ORBIS-only maintenance burden and matches what iOS will require
  anyway.

**Trade-offs explicitly accepted:**

- No browser/PWA access. Users who want to use ORBIS from a phone-not-
  on-tailnet browser are out of luck. (Mitigation: iOS app is on the
  roadmap; tailnet remains the supported multi-device answer.)
- No Linux/Windows desktop in the Mac hardening pass. (Mitigation: Docker
  self-host stays documented until the native desktop ports are added.)
- Tighter coupling to Apple's audio stack. If Apple changes
  AVAudioEngine semantics, we follow. (Mitigation: AVAudioEngine has
  been stable since 10.10; voice-processing IO since 10.13.)
- Migration work in three phases is non-trivial. We accept this in
  exchange for permanent simplification.

---

## What changes — concrete file/feature deletes (Phase 1)

| Removed | Lines | Why |
|---|---|---|
| `vite-plugin-pwa` plugin + `manifest.webmanifest` + `sw.js` registration | ~1 dep, build step, 60 LoC config | Local-only Tauri shell gains nothing from a service worker. PWA caching was the source of half today's "Load failed" debugging. |
| `@pipecat-ai/client-react`, `@pipecat-ai/client-js`, `@pipecat-ai/small-webrtc-transport` from `web/package.json` | ~MBs of bundle weight, ~3 deps | WebRTC client stack. Dead weight without a WebRTC peer. |
| `voice/multi_input_mixer.py` | ~170 LoC | Exists *only* to arbitrate CPAL + WebRTC mics simultaneously. With WebRTC gone, it deletes outright. |
| WebRTC dual-flush branch in `voice/native_bargein.py` | ~30 LoC | Only the CPAL ring needs flushing. |
| `web/src/shared/audio/MicTest.tsx` + `recordWav.ts` (`getUserMedia`) | ~200 LoC + tests | Voiceprint enrollment uses these; replaced by `NativeLevelMeter` (already calls Tauri IPC). |
| `OrbStage.tsx` WebRTC-click handler + `VoiceStateBridge.tsx` WebRTC branches | ~50 LoC | All `audioTransport === 'webrtc'` branches go. |
| `/api/offer` (WebRTC signalling) endpoint in `app.py` | ~40 LoC | WebRTC plumbing only. |
| `src-tauri/src/media_permission_patch.m` (UIDelegate Grant→Prompt swap) | ~50 LoC of Obj-C runtime hackery | Without `getUserMedia`, irrelevant. |
| `voice/transport_factory.py` factory branching | ~30 LoC | Always native; just import `LocalAudioTransport`. |
| `SmallWebRTCTransport` import + `_LocalAudioTransport_` instanceof guards | ~20 LoC scattered | Pipeline is no longer forked on transport. |
| `AUDIO_TRANSPORT` env var | n/a | Implicit `native`. |
| `web/src/voice/useNativeBridge.ts` | not deleted — promoted to default and renamed `useVoiceBridge.ts` | SSE bridge becomes the only voice-state path. |

**Round number: ~600+ LoC deleted, several MB off the JS bundle, and
the entire conceptual overhead of "WebRTC vs native mode" carved out
of the codebase.**

## What stays in Phase 1

- Pipecat 1.1.x as orchestrator (still useful for STT → LLM → TTS).
- Python sidecar via PyApp (works fine; protoApp consolidation can
  wait).
- React frontend served by Python sidecar.
- `voice/local_transport.py` — no Pipecat equivalent; this is the
  novel work.
- `voice/native_bargein.py` (CPAL ring flush only after WebRTC delete).
- `voice/sse_bus.py` — but the `SseBusObserver` should be refactored
  to subclass `RTVIObserver` (Phase 1 sub-item) so we stop forking the
  RTVI event vocabulary.
- `src-tauri/src/audio/{engine,socket,aec}.rs` — kept for now; replaced
  in Phase 2.
- `mic_permission.m` — `AVCaptureDevice.requestAccess` shim still
  needed for TCC registration (no upstream Tauri replacement).

---

## Phase 1 — high-ROI cleanup actions (synthesis of all three research streams)

Ordered by ROI. Items 1–4 should be done together as a focused PR.
Items 5+ can be split.

### 1. Replace `rm -rf ~/Library/WebKit/<bid>` with `Webview::clear_all_browsing_data()`
- Source: stream 2. Rust API has existed since Tauri 2.0.
- Cost: half-day.
- Effect: eliminates the entire "Load failed after rebuild" class of
  bug at the API layer. Update `scripts/nuke-and-rebuild.sh` to call
  it via a `--reset` CLI arg or a debug-build menu item.

### 2. Drop `aec.rs` (187 LoC) → adopt `webrtc-audio-processing 2.0.4`
- Source: stream 1. v2.0.4 shipped 2026-04-16. Provides AEC + AGC +
  NS + VAD in one crate.
- Cost: weekend, **net −70 LoC**.
- Effect: real Apple-quality AEC, eliminates today's 8× software gain
  hack and STT_MIN_RMS gates. Backchannel + microack come back online
  cleanly because echo bleed no longer false-triggers VAD.
- **Note:** Phase 2 will replace this with AVAudioEngine voice-
  processing IO. webrtc-audio-processing is a 2-week interim that's
  cheap to delete later.

### 3. Bump `cpal 0.15.3 → 0.17.3`
- Source: stream 1. `Stream` is `Send` on all hosts since 0.17.0
  (issue [#818](https://github.com/RustAudio/cpal/issues/818) closed
  2025-03-15).
- Cost: weekend, ~50 LoC.
- Effect: drops `unsafe impl Send for AudioEngine`, exposes
  `ErrorKind::DeviceChanged` for AirPods hot-swap, picks up CoreAudio
  improvements.

### 4. Ad-hoc sign every dev build with stable `--identifier`
- Source: stream 2.
- Cost: 1 day. Add a `beforeBundleCommand` that runs
  `codesign --force --deep --sign - --identifier studio.protolabs.orbis ...`
- Effect: kills the two-bundle-ID drift (`studio.protolabs.orbis` vs
  `orbis-tauri`) AND stabilizes TCC across rebuilds (no more re-prompts
  on every `cargo tauri build`).

### 5. `tauri-plugin-log 2.8.0` with rotating `LogDir`
- Source: stream 2. Tee `CommandEvent::Stdout/Stderr` via
  `log::info!(target:"sidecar", ...)`.
- Cost: 1–2 days.
- Effect: unifies Rust + frontend + sidecar stdio into one rotating
  `~/Library/Logs/.../orbis.log`. Replaces today's split between
  `/tmp/orbis-tauri.stderr` (Rust) and `~/Library/Logs/.../sidecar.log`
  (Python).

### 6. `SseBusObserver` → subclass `RTVIObserver`
- Source: stream 3.
- Cost: half-day.
- Effect: stops forking the RTVI event vocabulary. Future
  `pipecat-client-react` adoption becomes a drop-in. Override
  `_push_transport_message_frame` (or hook `send_server_message`)
  to fan out via `sse_bus` in addition to (or instead of)
  `OutputTransportMessageUrgentFrame`.

### 7. Move `rubato::resample_linear` out of audio callback
- Source: stream 1. Mirrors `cjpais/Handy` (the 20.7k-star reference).
- Cost: half-day, ~30 LoC.
- Effect: build `FftFixedIn` once outside the callback; no allocations
  in real-time path. Phase 2 may delete this if AVAudioEngine handles
  rate matching internally.

### 8. `selfDestroying: true` on `vite-plugin-pwa`
- Source: stream 2.
- Cost: half-day.
- Effect: any user with the PWA installed gets it cleaned up on next
  launch. Then remove the plugin entirely in the same PR as the rest
  of the web carve.

### 9. `enable_rtvi=False` on `PipelineTask`
- Source: stream 3. Silences "RTVIProcessor and RTVIObserver found,
  skipping default ones" boot warning.
- Cost: 1 line.
- Effect: signal-to-noise win in logs. We already construct both
  manually at `app.py:1110, 1256`.

### 10. CASTER 20-channel broadcast bug
- Source: stream 1.
- Cost: <1 hour, 5 LoC.
- Effect: write mono TTS to `frame[0]` only, zero the rest. Stops
  routing audio to all 20 outputs of multi-channel USB interfaces.

### 11. Pipecat 1.1.0 SCTP MTU fix [PR #4358](https://github.com/pipecat-ai/pipecat/pull/4358)
- Status: already in 1.1.0 (today's pin).
- Effect: prevents infinite-retransmit stall over Tailscale / IPv6 /
  VPN MTUs for any future remote-PWA scenario. Worth a STATUS.md note.

---

## Phase 2 — Apple-native audio (Mac hardening)

### Migrate input from CPAL → AVAudioEngine voice-processing IO

- Crate: `objc2-avf-audio` ([docs](https://docs.rs/objc2-avf-audio)).
- Reference: WWDC23-10235 ("What's new in voice processing"), Apple
  forum thread [733733](https://developer.apple.com/forums/thread/733733)
  (acknowledges the AGC trade-off — which is *exactly* what we want).
- API: `AVAudioEngine` + `setVoiceProcessingEnabled(true)` on the
  input node. Apple gives us AEC + NS + AGC for free.

### What deletes in Phase 2

- `src-tauri/src/audio/aec.rs` (already replaced by `webrtc-audio-
  processing` in Phase 1, now deleted entirely).
- `src-tauri/src/audio/engine.rs` input path (CPAL → AVAudioEngine).
  Output stays on CPAL or also moves — designer's call.
- `voice/local_transport.py` legacy CPAL `MIC_GAIN` software boost — Apple's
  AGC replaces it on the Mac voice-processing path.
- `voice/stt.py` `STT_MIN_RMS` / `STT_STRONG_RMS` / `STT_MIN_TEXT_LEN`
  gates — Apple's NS + Whisper's natural performance handle hallucination
  from real silence.
- The hallucination phrase blocklist — keep as a defense-in-depth.

### What we re-enable in Phase 2

- Backchannel + microack (currently disabled in native mode in
  `app.py`'s `behavior` resolution). With real AEC, these don't
  false-trigger on bot tail.
- Default VAD thresholds — back toward `confidence=0.7, min_volume=0.6`
  (pipecat defaults).

---

## Phase 3 — protoApp consolidation (Q2, weeks)

Adopt the Cargo workspace pattern from `github.com/protoLabsAI/protoApp`:

- Pull `protolabs-voice-core` in as a Cargo dep (or vendor as a git
  submodule).
- Migrate ORBIS Python sidecar → speak protoApp's WebSocket protocol
  (`orbis-sidecar` crate is already designed for this — see
  `protoApp/docs/how-to/integrate-orbis-sidecar.md`).
- Sidecar contract: bind WS on `127.0.0.1:<ephemeral>`, print
  `ORBIS_READY ws://127.0.0.1:<port>/<path>` to stdout, speak JSON
  `{type, text}`. Eliminates today's 8-byte-header binary protocol
  and 295-line `local_transport.py`.
- PyApp content-hash cache problem disappears (sidecar is now plain
  `python -m orbis` over WS).

### What deletes in Phase 3

- `voice/local_transport.py` (entire file; replaced by orbis-sidecar
  WS contract).
- `src-tauri/src/audio/socket.rs` (entire file; replaced by orbis-
  sidecar's WS framing).
- `voice/native_bargein.py` (functionality moves to the protoApp host).
- `voice/sse_bus.py` (replaced by the WS event stream).

### What deletes from `nuke-and-rebuild.sh`

- pyapp env-cache wipe (no longer relevant).
- Rust audio binary staging (now lives in `protolabs-voice-core`).

---

## Phase 4 — iOS (Q3+)

Full migration to in-process Rust per protoApp's already-shipping
pattern:

- `whisper-rs 0.16` for STT.
- `kokoros` for TTS.
- `llama-cpp-2` (or successor) for LLM, with Metal feature on Apple
  Silicon.
- Tauri Mobile target (iOS in alpha as of 2.10.x).
- Python sidecar becomes desktop-only optional ("power-user delegate
  runtime" — agents that need Python deps users don't have).

This phase is the entire point of dropping the web target — every
dollar of complexity we remove from the desktop path makes the iOS
port practical.

---

## Things to leave alone (research-validated)

- **Tauri 2.10.3.** Current; no 2.11 / 3.0 release exists yet.
- **PyApp 0.29.0.** 6 months stale but not abandoned; defer migration
  unless cache wipes keep biting.
- **`mic_permission.m` `AVCaptureDevice.requestAccess` shim.** No
  upstream Tauri replacement; needed for TCC registration.
- **Pipecat 1.1.0.** Just landed 2026-04-27. We're current.
- **`voice/local_transport.py`, `voice/native_bargein.py`,
  `voice/sse_bus.py`** until Phase 3. No Pipecat equivalent for our
  desktop path; deleting prematurely is a regression.
- **CPAL on the output path.** Phase 2 may keep CPAL for output and
  only migrate input to AVAudioEngine — simpler, less risk.

---

## Open questions deferred

- **CPAL vs AVAudioEngine for output.** If output stays on CPAL, the
  20-channel CASTER bug fix (Phase 1 item #10) remains relevant. If
  output moves to AVAudioEngine, Apple handles channel routing.
- **`webrtc-audio-processing 2.0.4` vs jumping straight to AVAudioEngine.**
  Phase 1 + 2 are sequenced as "interim → permanent" — but if Phase 2
  starts immediately, Phase 1 item #2 is wasted work. Recommendation:
  do Phase 1 #2 anyway; AVAudioEngine adoption is non-trivial and
  having real AEC for 1–2 weeks is worth the temporary code.
- **iOS LLM choice.** llama-cpp-2 with Metal is fine for desktop, but
  iOS device thermal envelope is smaller. May want to default to a
  smaller quantization or fall back to a remote gateway on iOS.
- **Multi-device sync after iOS lands.** Tailnet works for desktop;
  doesn't on iOS without TailscaleVPN. Possibly migrate to
  Tailscale-on-iOS or a different multi-device story.

---

## References

### Today's research streams (2026-04-28)

Three parallel `general-purpose` agents ran with full web access. Full
transcripts are recoverable from the conversation history; key cites
inlined throughout this doc.

1. **Rust audio stack DD** — concluded: bump cpal 0.15→0.17, drop
   custom AEC for `webrtc-audio-processing 2.0.4`, move resampler out
   of callback, fix CASTER 20-channel broadcast. Reference apps:
   `cjpais/Handy` (Tauri+CPAL+Whisper, 20.7k★), `kstonekuan/tambourine-
   voice` (Tauri+Pipecat-via-WebRTC).
2. **Tauri 2 sidecar / audio DD** — concluded: Tauri 2.10.3 is
   current, replace shell-rm with `Webview::clear_all_browsing_data()`,
   ad-hoc sign with stable identifier, adopt `tauri-plugin-log 2.8.0`,
   `selfDestroying: true` on PWA. Unix socket for PCM is the right
   call (latency wins over TCP loopback).
3. **Pipecat 1.1.x native transport DD** — concluded: no Pipecat
   transport for "raw PCM over Unix socket" exists; reference desktop
   apps all use SmallWebRTC over localhost. ORBIS's `local_transport.py`
   is novel work, not wheel-reinvention. The one piece worth migrating
   is `SseBusObserver` → subclass `RTVIObserver` to stop forking RTVI
   event names.

### Prior art

- **`github.com/protoLabsAI/protoApp`** — sibling repo. Tauri 2 +
  Cargo workspace + in-process Rust LLM/STT/TTS via `protolabs-voice-
  core`. Has `orbis-sidecar` crate documenting the WebSocket
  contract for ORBIS-style Python sidecars. The migration target.
- **`github.com/cjpais/Handy`** — reference Tauri voice app. CPAL
  on a dedicated thread with `mpsc::channel<Cmd>` for control —
  the pattern that avoids `unsafe impl Send`.
- **`github.com/kstonekuan/tambourine-voice`** — reference Tauri +
  Pipecat dictation app. Chose `SmallWebRTCTransport` over a
  Rust-side audio bridge; got AEC + AGC + NS + VAD + jitter buffer
  + network resilience for free from WebRTC. The architectural
  alternative we're not taking.

### Pipecat sources

- [Pipecat 1.1.0 release](https://github.com/pipecat-ai/pipecat/releases/tag/v1.1.0)
- [Pipecat 1.0 migration guide](https://docs.pipecat.ai/pipecat/migration/migration-1.0)
- [LocalAudioTransport (PyAudio)](https://github.com/pipecat-ai/pipecat/blob/main/src/pipecat/transports/local/audio.py)
  — the only "no WebRTC" transport Pipecat ships; not a fit because
  it's PyAudio in-process.
- [RTVI standard](https://docs.pipecat.ai/client/rtvi-standard)

### Apple sources

- [WWDC23-10235 — What's new in voice processing](https://developer.apple.com/videos/play/wwdc2023/10235/)
- [Using voice processing](https://developer.apple.com/documentation/avfaudio/audio_engine/audio_units/using_voice_processing)
- [`objc2-avf-audio`](https://docs.rs/objc2-avf-audio)

### Tauri sources

- [`Webview::clear_all_browsing_data`](https://v2.tauri.app/reference/javascript/api/namespacewebview/)
  — Rust + JS API since Tauri 2.0.
- [`tauri-plugin-log 2.8.0`](https://github.com/tauri-apps/plugins-workspace/releases/tag/log-v2.8.0)
- [Sidecar lifecycle plugin proposal #3062](https://github.com/tauri-apps/plugins-workspace/issues/3062)
  — open, not yet implemented; use `sysinfo` for descendant cleanup
  in the meantime.

### Rust audio sources

- [cpal 0.17 UPGRADING.md](https://github.com/RustAudio/cpal/blob/v0.17.0/UPGRADING.md)
- [cpal #818 — Implement Send for Stream](https://github.com/RustAudio/cpal/issues/818)
- [tonarino/webrtc-audio-processing 2.0.4](https://github.com/tonarino/webrtc-audio-processing/releases)
- [HEnquist/rubato](https://github.com/HEnquist/rubato/releases)
