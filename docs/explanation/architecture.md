# Architecture

## System diagram

```
┌─────────────────┐           ┌─────────────────────────────────────┐
│  Browser        │  WebRTC   │  protovoice container (GPU 0)        │
│  (mic + spk)    │◄─────────►│                                      │
│                 │           │  FastAPI :7866                       │
│                 │◄──HTTP───►│    /api/offer (POST/PATCH)            │
└─────────────────┘           │    /api/verbosity                    │
                              │    /healthz                          │
                              │                                      │
                              │  SmallWebRTCTransport                │
                              │      │                               │
                              │      ▼                               │
                              │  Silero VAD + Whisper large-v3-turbo │
                              │      │                               │
                              │      ▼                               │
                              │  OpenAILLMService ───► vLLM :8100    │
                              │      │      (or LLM_URL gateway)      │
                              │      ▼                               │
                              │  FishAudioTTS ──► HTTP :8092 ──┐      │
                              │  OR LocalKokoroTTS (in-proc)   │      │
                              │      │                         │      │
                              └──────┼─────────────────────────┼──────┘
                                     │                         │
                                     │                         ▼
                                     │      ┌─────────────────────┐
                                     │      │  fish-speech (GPU 1) │
                                     │      │  tools.api_server    │
                                     │      │  --half --compile    │
                                     │      └─────────────────────┘
                                     ▼
                               (audio back to browser)
```

## Why two containers?

**Dependency isolation.** Fish Audio ships its own `.venv` with pinned torch, VQ-VAE, llama decoder, and codec models. Jamming it into the same Python environment as vLLM creates dep conflicts that take a week to unwind. Separate containers keep the matrix small.

**GPU separation.** Fish S2-Pro at `--compile` wants ~22 GB + compile memory. Whisper (~6 GB) + vLLM routing (~15 GB) + Kokoro fallback (~2 GB) want ~23 GB. Two GPUs, one workload each.

**Restartability.** Fish's ~2-minute cold compile happens in its container. Restarting the voice agent doesn't retrigger it. Restarting Fish doesn't tear down the voice agent either.

## Why FastAPI under Pipecat?

Pipecat's `SmallWebRTCTransport` doesn't ship a server; it's a library you mount on whatever HTTP framework you want. We use FastAPI so we can host, alongside the voice pipeline:

- WebRTC signalling — `POST /api/offer` + `PATCH /api/offer` (trickle ICE).
- Session control — `POST /api/verbosity`, `POST /api/skills`, `POST /api/voice/clone`, `GET /healthz`, `GET /metrics`.
- Inbound A2A JSON-RPC — `POST /a2a` handles both `message/send` (sync) and `message/stream` (SSE) per spec; the text agent runs a bounded ReAct loop so external fleet agents can use our tool registry. See [A2A Integration](/guides/a2a-integration).
- A2A push callbacks — `POST /a2a/push` (spec-conformant) and `POST /a2a/callback` (legacy permissive shape).
- Agent card — `GET /.well-known/agent.json` for A2A discovery.
- The static HTML client served from `static/`.

The pipeline itself runs inside a `PipelineTask` spawned per connected WebRTC peer; text-only A2A traffic bypasses the pipeline entirely and calls the text agent directly.

## Network topology

Signalling (HTTPS for non-localhost clients) can go through any reverse proxy. Media (WebRTC UDP) must go directly browser ↔ server. Practical paths:

- **Same LAN** — direct.
- **Tailnet** — direct via `100.x` addresses. Works across the internet because Tailscale does the NAT traversal for us.
- **Internet with TURN** — not yet configured; planned for public deployment.

Signalling over HTTPS plus media over UDP is a hard split; Tailscale Funnel forwards HTTPS but does NOT relay arbitrary UDP back to the server, so Funnel is fine for signalling only. Tailscale Serve (tailnet-only HTTPS) works end-to-end because both peers sit on the tailnet.

## Connection lifecycle

1. Browser hits `GET /` → static HTML loads.
2. User clicks **Start** → `getUserMedia` → new `RTCPeerConnection` with audio + video transceivers.
3. Browser POSTs SDP offer → server creates a `SmallWebRTCConnection`, links it to a fresh `PipelineTask` with its own STT/LLM/TTS instances.
4. Browser PATCHes ICE candidates as they trickle in.
5. DTLS + SCTP + data channel open; RTP flows both ways.
6. User speaks → VAD fires `UserStartedSpeakingFrame` → STT accumulates audio until stop → `TranscriptionFrame` → LLM.
7. LLM streams `LLMTextFrame`s → TTS aggregates into sentences → `TTSAudioRawFrame` → transport → browser.
8. Browser disconnects → `on_client_disconnected` → `task.cancel()` → resources freed.

## Multi-user state

Each browser connection gets its own `PipelineTask` with fresh service instances. Shared module-level state is limited to:

- `VERBOSITY` (filler settings) — shared, will be per-session later
- vLLM subprocess — shared, stateless per request
- Fish sidecar connection — shared, stateless per request
- Whisper / Kokoro HF models — shared, loaded once, stateless

No session state is persisted across browser reconnects yet. Memory and skill personas land in later milestones.
