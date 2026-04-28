# Native CPAL Audio Transport

**Status:** Planned  
**Decision date:** 2026-04-27

## Goal

Replace browser WebRTC audio on desktop with native Rust/CPAL while keeping WebRTC as a permanent first-class path for browser/PWA clients (phone via Tailscale, etc). Both transports feed the **same shared Pipecat pipeline session**.

## Final Architecture

```
macOS mic (CoreAudio/CPAL)
  → Rust audio thread (ring buffer)
  → AEC (speexdsp — reference signal is TTS playback PCM)
  → Unix socket /tmp/orbis-audio-{pid}.sock
  → Python LocalAudioTransport → MultiInputMixer
                                        ↓
                              Shared Pipecat pipeline
                              (VAD → STT → LLM → Kokoro)
                                        ↓
                              TeeFrameProcessor
                             ↙                  ↘
          LocalAudioOutputTransport      WebRTCOutputBridge
                 ↓                              ↓
        Unix socket → Rust CPAL       SmallWebRTCTransport
              playback                   → browser/PWA

Browser/PWA mic (getUserMedia → WebRTC)
  → SmallWebRTCTransport → MultiInputMixer (same pipeline)
```

**Session model:** One persistent pipeline. Desktop is the native client. Phone/browser connects via WebRTC as a second concurrent input/output. Barge-in from either source flushes both outputs.

## Feature Flags

- Rust: `native-audio` Cargo feature (gates all new audio code)
- Python: `AUDIO_TRANSPORT=native|webrtc` env var
- Frontend: `VITE_AUDIO_TRANSPORT=native|webrtc` build-time env var

## Phases

### Phase 1 — Rust CPAL Engine + IPC
Files to create:
- `src-tauri/src/audio/mod.rs`
- `src-tauri/src/audio/engine.rs` — CPAL capture (16kHz mono s16le, 320 samples/20ms) + playback (24kHz mono s16le), rubato resampler if device rate differs
- `src-tauri/src/audio/aec.rs` — delay-subtract AEC (Phase 1), upgraded to speexdsp in Phase 3
- `src-tauri/src/audio/socket.rs` — Unix socket server, 8-byte framed PCM wire protocol

Files to modify:
- `src-tauri/Cargo.toml` — add `cpal = "0.15"`, `rubato = "0.15"` under `native-audio` feature
- `src-tauri/src/lib.rs` — start engine + socket server when `AUDIO_TRANSPORT=native`, pass `ORBIS_AUDIO_SOCK` env var to sidecar

Wire protocol (8 bytes header, little-endian):
```
[0..2]  u16  direction: 0x0001=mic→python, 0x0002=python→speaker, 0x0010=control
[2..4]  u16  sample_rate: 16000 | 24000
[4..6]  u16  channels: 1
[6..8]  u16  num_samples
body:   num_samples * 2 bytes i16 PCM

control frame body (direction=0x0010):
[0..2]  u16  0x0001=barge-in interrupt, 0x0002=TTS end
[2..8]  reserved
```

Socket path: `$TMPDIR/orbis-audio-{pid}.sock`

### Phase 2 — Python LocalAudioTransport
Files to create:
- `voice/local_transport.py` — `LocalAudioInputTransport`, `LocalAudioOutputTransport`, `LocalAudioTransport` (combined)
- `voice/transport_factory.py` — `make_transport()` — returns `LocalAudioTransport` or `SmallWebRTCTransport` based on env

Files to modify:
- `app.py` — `run_bot()` accepts pre-built transport; in native mode starts persistent pipeline task in lifespan; `EchoGuard guard_ms=0` in native mode; `GET /healthz` returns `audio.transport`

### Phase 3 — Real AEC + Barge-in
Files to create:
- `voice/native_barge_in.py` — `NativeBargeInObserver` sends control frame `0x0001` on `BotStoppedSpeakingFrame`

Files to modify:
- `src-tauri/src/audio/aec.rs` — replace delay-subtract with `speexdsp-sys` wrapper
- `src-tauri/Cargo.toml` — add `speexdsp-sys = "0.5"` under `native-audio` feature
- `.github/workflows/desktop-build.yml` — add `brew install speex && SPEEXDSP_STATIC=1`
- `app.py` — add `NativeBargeInObserver` to `PipelineTask` observers in native mode

### Phase 4 — TTS Fanout (Shared Session)
Files to create:
- `voice/tee_processor.py` — `TeeFrameProcessor` duplicates `TTSAudioRawFrame` to CPAL + WebRTC sinks
- `voice/multi_input_mixer.py` — `MultiInputMixer` merges mic frames from CPAL and WebRTC; energy-based per-20ms source selection

Files to modify:
- `app.py` — insert `MultiInputMixer` before VAD, insert `TeeFrameProcessor` after Kokoro; extend `NativeBargeInObserver` to flush both outputs

### Phase 5 — Frontend Wiring
Files to create:
- `web/src/voice/NativeVoiceStateBridge.tsx` — SSE subscriber, updates voiceStore

Files to modify:
- `app.py` — add `GET /api/voice/events` SSE, `POST /api/session/toggle`, `POST /api/audio/device`
- `web/src/App.tsx` — branch on `VITE_AUDIO_TRANSPORT`: native renders `NativeVoiceStateBridge` instead of `PipecatClientProvider`+`Audio`; orb double-click POSTs to `/api/session/toggle`
- `web/src/plugins/orb/OrbStage.tsx` — native connect/disconnect via fetch, not `client.connect()`
- `web/src/plugins/setup-wizard/SetupWizard.tsx` — device picker POSTs to `/api/audio/device`
- `web/src/plugins/settings-panel/MicSettings.tsx` — same

**Not changed:** `MicTest.tsx` — still uses `getUserMedia` for level meter only (fine)

## Key Risks

| # | Risk | Mitigation |
|---|------|------------|
| R1 | CPAL device sample rate mismatch | rubato resampler; log warning; test explicitly |
| R2 | Unix socket collision (two instances) | PID in socket path |
| R3 | TTS 24kHz → device rate resampling | rubato on playback path too; test with 44100Hz device |
| R4 | Pipeline lifecycle (persistent vs per-connection) | `LocalAudioTransport.close()` must emit disconnect event |
| R5 | Orb audio envelope goes dead in native mode | Follow-on: `GET /api/voice/level` SSE with RMS values |
| R6 | speexdsp static linking in bundled .dmg | `SPEEXDSP_STATIC=1` in CI; `brew install speex` before build |
| R7 | MultiInputMixer double-talk (both mics hot simultaneously) | Energy-based selection; future: speexdsp echo between the two mic streams |

## Dependencies

### Rust (Cargo)
```toml
[features]
native-audio = ["dep:cpal", "dep:rubato", "dep:speexdsp-sys"]

[dependencies]
cpal          = { version = "0.15", optional = true }
rubato        = { version = "0.15", optional = true }
speexdsp-sys  = { version = "0.5",  optional = true }  # Phase 3
```

### Python
No new packages. Uses `asyncio.open_unix_connection` (stdlib) and existing pipecat base classes.

### Frontend
No new npm packages. `VITE_AUDIO_TRANSPORT` is a Vite build env var.
