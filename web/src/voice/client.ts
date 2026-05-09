import { PipecatClient } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';
import { apiKeyStore } from '@/auth/apiKey';
import { pairingStore } from '@/auth/pairing';
import { apiUrl } from '@/lib/backend';
import {
  getPreferredAudioDeviceId,
  subscribePreferredAudioDeviceId,
} from '@/shared/audio/preferredDevice';

// Transport states (from pipecat client docs) where `updateMic()` is
// safe to call — the local input pipeline exists and the transport is
// past the initialize phase. "initializing" and "disconnected" still
// throw with NotSupportedError because the device manager hasn't been
// constructed yet.
const MIC_READY_STATES = new Set([
  'initialized',
  'authenticating',
  'authenticated',
  'connecting',
  'connected',
  'ready',
]);

/**
 * Build a PipecatClient wired to ORBIS's SmallWebRTCRequestHandler.
 *
 * We POST an SDP offer to `/api/offer` and PATCH ICE updates to the same
 * path — the transport library handles the full handshake. The owner
 * API key is attached as ``X-API-Key`` so tailnet-hosted instances
 * authenticate the handshake; ``apiKeyStore`` returns null in
 * single-user fallback mode and the server accepts anonymously.
 *
 * The video transceiver stays enabled in the offer even though we only
 * send audio — omitting it causes DTLS/SCTP to silently fail on aiortc
 * (ORBIS's WebRTC backend). `enableCam: false` keeps the camera off
 * while still negotiating the transceiver.
 */
export function buildClient(): PipecatClient {
  const key = apiKeyStore.get();
  const headers = new Headers();
  if (key) headers.set('X-API-Key', key);
  // Cross-origin pairing token (empty in same-origin installs) — the
  // sidecar's middleware enforces it on /api/* including /api/offer
  // when ORBIS_ALLOWED_ORIGINS is set.
  const pair = pairingStore.get();
  if (pair) headers.set('X-Orbis-Pair', pair);

  const transport = new SmallWebRTCTransport({
    webrtcRequestParams: {
      // apiUrl() resolves to '/api/offer' (same-origin, default) or
      // '<backendBaseUrl>/api/offer' for the hosted-SPA + local-sidecar
      // topology. Pipecat's transport treats the endpoint string as
      // opaque, so an absolute URL works fine.
      endpoint: apiUrl('/api/offer'),
      headers,
    },
    waitForICEGathering: true,
  });
  // Latched mic id — updated by the localStorage subscriber, applied
  // once the transport reaches an `updateMic`-safe state. We never
  // drop a desired id; the latest write wins on every state-change
  // tick until the transport accepts it.
  let pendingDeviceId: string | null = getPreferredAudioDeviceId() || null;
  let micApplied = false;

  const applyMicIfReady = (state: string, client: PipecatClient) => {
    if (!MIC_READY_STATES.has(state)) return;
    const id = pendingDeviceId;
    if (id === null) {
      micApplied = true;
      return;
    }
    try {
      client.updateMic(id);
      micApplied = true;
    } catch {
      // Transport flapped between the state event and the call —
      // leave `micApplied=false` so the next state tick retries.
    }
  };

  const client = new PipecatClient({
    transport,
    enableMic: true,
    enableCam: false,
    callbacks: {
      // Apply the pending mic id once the transport is initialized
      // enough to honor it. Pipecat only fires this for transitions,
      // not the initial state, so the subscribe below also probes
      // the current state on later writes.
      onTransportStateChanged: (state: string) => {
        if (!micApplied) applyMicIfReady(state, client);
      },
    },
  });

  // Plumb the user's selected mic into Pipecat. The picker writes to
  // localStorage via `setPreferredAudioDeviceId`; we update the latched
  // value and re-attempt applying it. Empty string means "system
  // default" — Pipecat treats that as no override, but we still want
  // to clear any prior selection, so we pass it through.
  subscribePreferredAudioDeviceId((id) => {
    pendingDeviceId = id || null;
    micApplied = false;
    // Try eagerly — if we're already ready, this lands immediately.
    // The transport exposes its current state via `state` getter on
    // versions that support it; fall back to the callback path.
    const current = (client as unknown as { state?: string }).state;
    if (current) applyMicIfReady(current, client);
  });

  return client;
}
