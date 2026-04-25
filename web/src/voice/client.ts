import { PipecatClient } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';
import { apiKeyStore } from '@/auth/apiKey';
import {
  getPreferredAudioDeviceId,
  subscribePreferredAudioDeviceId,
} from '@/shared/audio/preferredDevice';

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

  const transport = new SmallWebRTCTransport({
    webrtcRequestParams: {
      endpoint: '/api/offer',
      headers,
    },
    waitForICEGathering: true,
  });
  const client = new PipecatClient({
    transport,
    enableMic: true,
    enableCam: false,
    callbacks: {},
  });

  // Plumb the user's selected mic into Pipecat. The picker writes to
  // localStorage via `setPreferredAudioDeviceId`; we apply the current
  // value at build time and subscribe so any later change in the
  // settings panel or wizard reaches the live client without a
  // reconnect. Empty string means "system default" — Pipecat treats
  // that as no override.
  const initialId = getPreferredAudioDeviceId();
  if (initialId) {
    try {
      client.updateMic(initialId);
    } catch {
      // updateMic can throw before the transport is ready; the
      // subscriber below picks up the same value once Pipecat is
      // primed if the user touches the picker again.
    }
  }
  subscribePreferredAudioDeviceId((id) => {
    try {
      client.updateMic(id);
    } catch {
      // Ignored: same reason as above. Worst case the user re-selects
      // and the next call lands.
    }
  });

  return client;
}
