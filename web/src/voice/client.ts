import { PipecatClient } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';

/**
 * Build a PipecatClient wired to protoVoice's SmallWebRTCRequestHandler.
 *
 * We POST an SDP offer to `/api/offer` and PATCH ICE updates to the same
 * path — the transport library handles the full handshake.
 *
 * The video transceiver stays enabled in the offer even though we only
 * send audio — omitting it causes DTLS/SCTP to silently fail on aiortc
 * (protoVoice's WebRTC backend). `enableCam: false` keeps the camera off
 * while still negotiating the transceiver. See
 * `project_pipecat_gotchas.md` line 16 for the forensic.
 */
export function buildClient(): PipecatClient {
  const transport = new SmallWebRTCTransport({
    webrtcRequestParams: {
      endpoint: '/api/offer',
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
