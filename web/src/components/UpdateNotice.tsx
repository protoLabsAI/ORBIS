import { useEffect, useState } from 'react';
import { check, type Update } from '@tauri-apps/plugin-updater';
import { relaunch } from '@tauri-apps/plugin-process';
import { Button } from '@/components/ui/button';

/**
 * In-app update notice. Tauri's updater checks the signed `latest.json` on the
 * GitHub release; when a newer version is published this surfaces a subtle pill
 * (ambient, like the status pill — never a modal that interrupts a voice turn).
 * Click it for the notes + a one-click "Update & Restart". Stays silent in dev /
 * non-Tauri / offline / when there's no update.
 *
 * User-driven by design (approach A): we detect + notify, the user chooses when
 * to apply. No silent background install.
 */

const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000; // re-check every 6h
const FIRST_CHECK_MS = 10_000; // let the boot settle first

type Phase = 'available' | 'downloading' | 'error';

export function UpdateNotice() {
  const [update, setUpdate] = useState<Update | null>(null);
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>('available');
  const [pct, setPct] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (cancelled || update) return;
      try {
        const u = await check();
        if (!cancelled && u) setUpdate(u);
      } catch {
        // Not in Tauri, no manifest yet, or offline — stay quiet.
      }
    };
    const first = window.setTimeout(run, FIRST_CHECK_MS);
    const timer = window.setInterval(run, CHECK_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [update]);

  if (!update) return null;

  const install = async () => {
    setPhase('downloading');
    setError(null);
    setPct(0);
    try {
      let total = 0;
      let got = 0;
      await update.downloadAndInstall((e) => {
        if (e.event === 'Started') total = e.data.contentLength ?? 0;
        else if (e.event === 'Progress') {
          got += e.data.chunkLength;
          if (total > 0) setPct(Math.min(100, Math.round((got / total) * 100)));
        }
      });
      await relaunch();
    } catch (e) {
      setPhase('error');
      setError(String((e as Error).message ?? e));
    }
  };

  // Collapsed: an ambient pill, top-right under the title bar.
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed right-3 top-9 z-40 flex items-center gap-1.5 rounded-full border border-brand/40 bg-brand/10 px-3 py-1 text-helper text-brand backdrop-blur-sm transition-colors hover:bg-brand/20"
        aria-label={`Update available: ${update.version}`}
      >
        <span className="h-1.5 w-1.5 rounded-full bg-brand" />
        Update · {update.version}
      </button>
    );
  }

  return (
    <div className="fixed right-3 top-9 z-40 w-80 max-w-[calc(100vw-1.5rem)] rounded-lg border border-edge bg-raised/95 p-4 shadow-xl backdrop-blur-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm text-fg-body">
          Update available
          <span className="ml-1.5 font-mono text-xs text-brand">{update.version}</span>
        </div>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-helper text-fg-subtle hover:text-fg-body"
          aria-label="Dismiss"
        >
          ✕
        </button>
      </div>

      {update.body ? (
        <div className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap text-helper text-fg-muted">
          {update.body}
        </div>
      ) : (
        <p className="mt-2 text-helper text-fg-muted">A newer version of ORBIS is ready.</p>
      )}

      {phase === 'downloading' && (
        <div className="mt-3 space-y-1.5">
          <div className="h-1 rounded-full bg-edge overflow-hidden">
            <div
              className="h-full rounded-full bg-brand transition-[width] duration-200"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="text-center text-helper text-fg-subtle tabular-nums">
            Downloading… {pct}%
          </div>
        </div>
      )}

      {phase === 'error' && error && (
        <p className="mt-2 text-helper text-danger">Update failed: {error}</p>
      )}

      <div className="mt-3 flex items-center justify-end gap-2">
        {phase !== 'downloading' && (
          <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
            Later
          </Button>
        )}
        <Button size="sm" onClick={install} disabled={phase === 'downloading'}>
          {phase === 'downloading'
            ? 'Updating…'
            : phase === 'error'
              ? 'Retry'
              : 'Update & Restart'}
        </Button>
      </div>
    </div>
  );
}
