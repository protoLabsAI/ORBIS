import { useEffect, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { api, type HealthDelegate } from '@/lib/api';

const POLL_INTERVAL_MS = 60_000;
// A single failed probe is most often a transient blip (DNS jitter,
// container restart). Two in a row is the signal to surface — matches
// the "consecutive_failures > 1" threshold the backend uses for its
// own warning logs in agent/delegates.health_loop.
const DEGRADED_THRESHOLD = 2;

/**
 * Polls /healthz once a minute and renders a subtle warning banner
 * when one or more delegates have failed ≥2 consecutive background
 * probes. Sits below the ConnectionBanner in z-stack so a hard
 * connection error wins; this is for "configured-but-degraded" cases
 * where the user might try to delegate to something that's down.
 *
 * Auto-dismisses when the affected delegates recover (probe goes
 * green → consecutive_failures resets to 0). Manual dismiss persists
 * for the lifetime of the current SPA session — the banner re-shows
 * on the next *new* degradation, not on a still-degraded state.
 */
export function DelegateHealthBanner() {
  const [degraded, setDegraded] = useState<HealthDelegate[]>([]);
  const [dismissedFingerprint, setDismissedFingerprint] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await api.healthz();
        if (cancelled) return;
        const failing = (r.delegates ?? []).filter(
          (d) => d.ok === false && d.consecutive_failures >= DEGRADED_THRESHOLD,
        );
        setDegraded((prev) => {
          // Recovery — every previously-degraded delegate is now ok.
          // Drop the dismiss fingerprint so a *fresh* degradation later
          // in the same session gets to surface; without this, the
          // dismissed-once fingerprint persists and a later identical
          // degradation stays silently suppressed.
          if (prev.length > 0 && failing.length === 0) {
            setDismissedFingerprint(null);
          }
          return failing;
        });
      } catch {
        // /healthz unreachable — usually means the sidecar is down,
        // which the ConnectionBanner already covers. Don't double-warn.
      }
    };
    tick();
    const id = window.setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  if (degraded.length === 0) return null;

  // Fingerprint = sorted set of degraded names. A new degradation
  // (different set) re-shows the banner even if the user had
  // dismissed an older one. The recovery branch above also clears
  // the dismiss state so the same set re-failing later still surfaces.
  const fingerprint = degraded.map((d) => d.name).sort().join('|');
  if (dismissedFingerprint === fingerprint) return null;

  return (
    <div className="pointer-events-auto fixed inset-x-0 top-16 z-40 mx-auto max-w-2xl px-4">
      <div
        role="status"
        className="flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-950/80 px-4 py-3 text-xs text-amber-100 shadow backdrop-blur"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
        <div className="flex-1">
          <div className="font-medium text-amber-50">
            {degraded.length === 1
              ? `Delegate "${degraded[0].name}" is unreachable.`
              : `${degraded.length} delegates are unreachable.`}
          </div>
          <div className="mt-0.5 text-amber-100/80">
            The orb may fail to delegate until they recover. Check the
            Settings panel for details.
          </div>
        </div>
        <button
          type="button"
          onClick={() => setDismissedFingerprint(fingerprint)}
          className="-m-1 rounded p-1 text-amber-200/70 hover:bg-amber-900/60 hover:text-amber-100"
          aria-label="Dismiss"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

