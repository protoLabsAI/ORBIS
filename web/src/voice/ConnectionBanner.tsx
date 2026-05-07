import { useState } from 'react';
import { AlertCircle, MicOff, X, WifiOff } from 'lucide-react';
import { useVoiceStateSelector } from './hooks';
import type { DeviceErrorType } from './state';

/**
 * Top-of-screen banner that surfaces the two failure modes a first-
 * time user is most likely to hit and most likely to misread:
 *
 *   1. The browser denied microphone access. Without a banner the orb
 *      sits idle while the user wonders why nothing happens; on Safari
 *      especially the system permission prompt is easy to dismiss
 *      without registering it.
 *
 *   2. The WebRTC handshake failed or the data channel dropped (the
 *      transport reaches pipecat's `error` state). Same UX failure
 *      mode — the orb stops responding with no feedback.
 *
 * Stays out of the way otherwise: returns null when there's nothing
 * to surface, dismissable while a session is mid-flight, and clears
 * itself automatically once the underlying condition resolves (a
 * fresh connect that reaches `ready` flips ``connectionError`` off).
 */
export function ConnectionBanner() {
  const deviceError = useVoiceStateSelector((s) => s.deviceError);
  const connectionError = useVoiceStateSelector((s) => s.connectionError);
  const [dismissed, setDismissed] = useState(false);

  // Re-surface on each fresh error — track the "current" error fingerprint
  // so a new error after dismiss re-shows the banner.
  const fingerprint = `${deviceError?.type ?? ''}|${connectionError ? '1' : '0'}`;
  const [lastFingerprint, setLastFingerprint] = useState(fingerprint);
  if (fingerprint !== lastFingerprint) {
    setLastFingerprint(fingerprint);
    setDismissed(false);
  }

  if (dismissed) return null;
  if (!deviceError && !connectionError) return null;

  const content = deviceError
    ? deviceErrorCopy(deviceError.type)
    : { icon: WifiOff, title: 'Connection lost', body: 'The voice link to ORBIS dropped. Try reconnecting from the orb; if it keeps failing, check your network.' };

  const Icon = content.icon;

  return (
    <div className="pointer-events-auto fixed inset-x-0 top-0 z-50 mx-auto max-w-2xl px-4 pt-4">
      <div
        role="alert"
        className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-950/90 px-4 py-3 text-sm text-amber-100 shadow-lg backdrop-blur"
      >
        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
        <div className="flex-1">
          <div className="font-medium text-amber-50">{content.title}</div>
          <div className="mt-0.5 text-amber-100/90">{content.body}</div>
        </div>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          className="-m-1 rounded p-1 text-amber-200/70 hover:bg-amber-900/60 hover:text-amber-100"
          aria-label="Dismiss"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

interface BannerCopy {
  icon: typeof MicOff;
  title: string;
  body: string;
}

function deviceErrorCopy(type: DeviceErrorType): BannerCopy {
  switch (type) {
    case 'permissions':
      return {
        icon: MicOff,
        title: 'Microphone access denied',
        // Browser-agnostic instructions; site permissions live in
        // different menus on Chrome / Firefox / Safari, so we point at
        // the URL bar (universal) instead of a per-browser walkthrough.
        body: 'Click the lock or permissions icon in the address bar to allow microphone access, then refresh.',
      };
    case 'not-found':
      return {
        icon: MicOff,
        title: 'No microphone detected',
        body: 'Plug in or pair a microphone, then refresh. Bluetooth headsets sometimes need to be connected before opening this page.',
      };
    case 'in-use':
      return {
        icon: MicOff,
        title: 'Microphone is busy',
        body: 'Another app is using the microphone. Close it (often a video call or recording app) and try again.',
      };
    case 'undefined-mediadevices':
      return {
        icon: AlertCircle,
        title: 'Microphone not available in this browser',
        body: 'Open ORBIS in Chrome, Edge, Firefox, or Safari over HTTPS — getUserMedia requires a secure context.',
      };
    case 'constraints':
      return {
        icon: MicOff,
        title: 'Microphone unsupported',
        body: 'The selected microphone can’t produce the audio format ORBIS needs. Pick a different mic in Settings.',
      };
    case 'unknown':
    default:
      return {
        icon: AlertCircle,
        title: 'Microphone error',
        body: 'Something went wrong setting up your microphone. Refreshing usually clears it.',
      };
  }
}
