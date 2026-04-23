import { PipecatClient } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';
import { apiKeyStore } from '@/auth/apiKey';

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
  return new PipecatClient({
    transport,
    enableMic: true,
    enableCam: false,
    callbacks: {},
  });
}
