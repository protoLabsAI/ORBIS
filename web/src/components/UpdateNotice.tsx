import { useEffect, useState } from 'react';
import { check, type Update } from '@tauri-apps/plugin-updater';
import { relaunch } from '@tauri-apps/plugin-process';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Markdown } from '@/components/ui/Markdown';

/**
 * In-app update notice. Tauri's updater checks the signed `latest.json` on the
 * GitHub release; when a newer version is published this surfaces a subtle pill
 * (ambient, like the status pill — never an unprompted modal over a voice turn).
 * Click it for a **full modal** with the release **changelog rendered as
 * markdown** + a one-click "Update & Restart". Stays silent in dev / non-Tauri /
 * offline / when there's no update.
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

  const downloading = phase === 'downloading';

  return (
    <>
      {/* Ambient pill, top-right under the title bar — opens the changelog modal. */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed right-3 top-9 z-40 flex items-center gap-1.5 rounded-full border border-brand/40 bg-brand/10 px-3 py-1 text-helper text-brand backdrop-blur-sm transition-colors hover:bg-brand/20"
        aria-label={`Update available: ${update.version}`}
      >
        <span className="h-1.5 w-1.5 rounded-full bg-brand" />
        Update · {update.version}
      </button>

      <Dialog open={open} onOpenChange={(next) => { if (!downloading) setOpen(next); }}>
        <DialogContent showCloseButton={!downloading}>
          <DialogHeader>
            <DialogTitle>
              What's new
              <span className="ml-1.5 font-mono text-xs text-brand">{update.version}</span>
              {update.currentVersion && (
                <span className="ml-1.5 text-helper font-normal text-fg-subtle">
                  · you have {update.currentVersion}
                </span>
              )}
            </DialogTitle>
          </DialogHeader>

          <div className="-mr-1 max-h-[55vh] overflow-y-auto pr-1">
            {update.body ? (
              <Markdown>{update.body}</Markdown>
            ) : (
              <p className="text-sm text-fg-muted">A newer version of ORBIS is ready.</p>
            )}
          </div>

          {downloading && (
            <div className="space-y-1.5">
              <div className="h-1 overflow-hidden rounded-full bg-edge">
                <div
                  className="h-full rounded-full bg-brand transition-[width] duration-200"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="text-center text-helper tabular-nums text-fg-subtle">
                Downloading… {pct}%
              </div>
            </div>
          )}

          {phase === 'error' && error && (
            <p className="text-helper text-danger">Update failed: {error}</p>
          )}

          <DialogFooter>
            {!downloading && (
              <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
                Later
              </Button>
            )}
            <Button size="sm" onClick={install} disabled={downloading}>
              {downloading ? 'Updating…' : phase === 'error' ? 'Retry' : 'Update & Restart'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
