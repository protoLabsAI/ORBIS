import { Mic, Shield, X } from 'lucide-react';
import { Button } from '@/components/ui/button';

/**
 * One-time rationale dialog shown before the very first ``getUserMedia``
 * call so users understand what they're being asked to allow before
 * the browser prompt fires. Without this, first-timers see a bare
 * "ORBIS wants to use your microphone" with zero context and often
 * deny out of caution — and once denied, recovery requires a trip
 * through browser settings.
 *
 * Skipped automatically when the permission state is already
 * ``granted`` (we don't need to re-explain on every connect) and
 * when it's ``denied`` (the OS prompt is suppressed; we route to the
 * ConnectionBanner instead).
 */
export interface MicPermissionRationaleProps {
  /** User clicked Continue — caller fires the actual connect/getUserMedia. */
  onContinue: () => void;
  /** User dismissed without continuing. Caller should keep the orb idle. */
  onCancel: () => void;
}

export function MicPermissionRationale({
  onContinue,
  onCancel,
}: MicPermissionRationaleProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="mic-rationale-title"
    >
      <div className="relative max-w-md w-full rounded-xl border border-zinc-800 bg-zinc-950 p-6 shadow-xl">
        <button
          type="button"
          onClick={onCancel}
          className="absolute right-3 top-3 rounded p-1 text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200"
          aria-label="Close"
        >
          <X className="h-4 w-4" />
        </button>

        <div className="flex items-start gap-3">
          <div className="rounded-full bg-amber-500/10 border border-amber-500/30 p-2 mt-0.5">
            <Mic className="h-5 w-5 text-amber-300" aria-hidden="true" />
          </div>
          <div className="flex-1 min-w-0">
            <h2
              id="mic-rationale-title"
              className="text-base font-medium text-zinc-100"
            >
              ORBIS needs your microphone
            </h2>
            <p className="text-sm text-zinc-400 mt-1.5 leading-relaxed">
              Your browser is about to ask for microphone access. ORBIS uses
              the mic to hear what you say so the orb can respond — that's
              the only way the voice loop works.
            </p>
          </div>
        </div>

        <ul className="mt-5 space-y-2.5 text-xs text-zinc-400">
          <PrivacyPoint>
            Audio streams to the ORBIS sidecar over a local WebRTC
            connection — same machine, same network.
          </PrivacyPoint>
          <PrivacyPoint>
            Nothing is recorded to disk by default; transcripts persist as
            text in your local SQLite store, not the audio.
          </PrivacyPoint>
          <PrivacyPoint>
            You can revoke at any time — Settings drawer → Mic, or your
            browser's site permissions.
          </PrivacyPoint>
        </ul>

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onCancel}>
            Not now
          </Button>
          <Button size="sm" onClick={onContinue}>
            Continue
          </Button>
        </div>
      </div>
    </div>
  );
}

function PrivacyPoint({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2">
      <Shield
        className="h-3.5 w-3.5 text-zinc-600 mt-0.5 shrink-0"
        aria-hidden="true"
      />
      <span className="leading-relaxed">{children}</span>
    </li>
  );
}
