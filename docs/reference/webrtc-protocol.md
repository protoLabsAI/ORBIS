# WebRTC Protocol

protoVoice uses [Pipecat's `SmallWebRTCTransport`](https://docs.pipecat.ai/server/services/transport/small-webrtc). This page documents the exact client-side flow our UI uses — if you're building a custom client, do this.

## Required transceivers

```js
pc.addTransceiver(audioTrack, { direction: "sendrecv" });
pc.addTransceiver("video",    { direction: "sendrecv" });
```

Both are required, even for an audio-only app. Omit the video transceiver and DTLS / SCTP negotiation silently fails — the connection will establish but no frames flow, and the server will log `Received an unexpected media stream error while reading the audio` after ~7 s.

## Trickle ICE

protoVoice supports trickle ICE via `PATCH /api/offer`. A candidate can't be sent before the POST answer returns with a `pc_id`, so the client should queue candidates that arrive early:

```js
const pendingCandidates = [];
let canSend = false;
let pcId = null;

pc.onicecandidate = async (e) => {
  if (!e.candidate) return;
  if (canSend && pcId) await sendIce(pcId, e.candidate);
  else pendingCandidates.push(e.candidate);
};

// ... after POST /api/offer returns ...
pcId = answer.pc_id;
canSend = true;
for (const c of pendingCandidates) await sendIce(pcId, c);
```

Non-trickle also works: wait for `iceGatheringState === "complete"` then POST the complete SDP. Same behaviour, simpler client.

## Data channel

Pipecat listens on `pc.on("datachannel", …)`. You do NOT need to `pc.createDataChannel(...)` — aiortc on the server side handles it, and the channel opens after DTLS. If the channel doesn't open within 10 s pipecat logs a warning but audio continues to flow.

## Audio codec

Opus via `RTCPeerConnection` defaults. Sample rate negotiated by the transport (typically 48 kHz input, resampled to 16 kHz for Whisper and back to 24 kHz or 44.1 kHz for TTS output).

## Full offer/answer round-trip

```
browser                                         server
  │                                               │
  │  POST /api/offer {sdp, type:"offer"}          │
  │──────────────────────────────────────────────►│
  │                                               │ instantiates SmallWebRTCConnection
  │                                               │ linkedinto PipelineTask
  │  {sdp, type:"answer", pc_id}                  │
  │◄──────────────────────────────────────────────│
  │                                               │
  │  pc.setRemoteDescription(answer)              │
  │                                               │
  │  PATCH /api/offer {pc_id, candidates:[…]}     │
  │──────────────────────────────────────────────►│
  │  {status:"success"}                           │
  │◄──────────────────────────────────────────────│
  │                                               │
  │  DTLS handshake over the selected ICE pair    │
  │──────────────────────────────────────────────►│
  │◄──────────────────────────────────────────────│
  │                                               │
  │  RTP audio packets both ways                  │
  │◄─────────────────────────────────────────────►│
  │                                               │
  │  Data channel opens (ping keepalive)          │
  │◄─────────────────────────────────────────────►│
```

## Network paths

WebRTC media is UDP direct between peers. Tunnelling signalling through an HTTPS proxy (Tailscale Funnel, Cloudflare Tunnel, nginx) only works if both peers can reach each other's advertised ICE candidates:

- **Same LAN** — `192.168.x.x` candidates work.
- **Tailnet (`100.64.0.0/10`)** — tailnet candidates work for any two tailnet peers. This is the recommended path for remote access.
- **Different networks, no TURN** — ICE will complete *signalling* but media won't flow. You need a TURN server.

See [Explanation → Architecture](/explanation/architecture) for the fuller network picture.
