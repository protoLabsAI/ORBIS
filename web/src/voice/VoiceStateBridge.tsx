import { useVoiceBridge } from './useVoiceBridge';

/**
 * Invisible component — drives the derived voiceStore from the SSE
 * event stream the Python sidecar publishes on /api/events.
 *
 * Mount once at the app root.
 *
 * Pre-2026-04-28 this also bridged WebRTC RTVI events into the same
 * store. The web/PWA path was dropped (DECISIONS.md amendment of that
 * date), and SSE is now the only path.
 */
export function VoiceStateBridge() {
  useVoiceBridge();
  return null;
}
