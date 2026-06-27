import { useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';

/**
 * Diagnostic actions surfaced to the user when something feels off.
 *
 * - "Reveal logs in Finder" — invokes the `reveal_logs` Tauri IPC, which
 *   opens the app log dir (sidecar.log + the Rust/tauri-plugin-log output)
 *   so a user can attach logs to a bug report. First slice of in-app
 *   diagnostics (#488); a bundled export-zip + crash reporting are the
 *   remaining work there.
 * - "Clear browsing data" — invokes `clear_browsing_data`, which calls
 *   `Webview::clear_all_browsing_data()` to wipe cookies, IndexedDB,
 *   localStorage, service-worker registrations, and the fetch cache. Use
 *   when the wizard or any /api/* fetch misbehaves after a sidecar version
 *   bump (stale SW / localStorage). The offline equivalent is
 *   `scripts/nuke-and-rebuild.sh`; this in-app button is the lighter-touch
 *   runtime fix that doesn't require closing the app.
 */
export function Diagnostics() {
  const [status, setStatus] = useState<'idle' | 'clearing' | 'cleared' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);

  const clear = async () => {
    setStatus('clearing');
    setError(null);
    try {
      await invoke('clear_browsing_data');
      setStatus('cleared');
      // Force a reload so the wiped storage is reflected in the UI.
      window.location.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus('error');
    }
  };

  // Opens the log folder (sidecar.log + the Rust/tauri log) in Finder so the
  // user can attach logs to a bug report without hunting for the path (#488).
  const revealLogs = async () => {
    setError(null);
    try {
      await invoke('reveal_logs');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus('error');
    }
  };

  return (
    <Panel title="Diagnostics">
      <div className="space-y-4">
        <div className="space-y-2">
          <p className="text-sm text-fg-muted leading-relaxed">
            Open the log folder in Finder (the Python sidecar log plus the Tauri shell
            log). Grab these when reporting a bug — transcripts and secrets are kept out
            of them.
          </p>
          <Button variant="outline" onClick={() => void revealLogs()}>
            Reveal logs in Finder
          </Button>
        </div>
        <div className="space-y-2">
          <p className="text-sm text-fg-muted leading-relaxed">
            Wipe the WKWebView's storage (cookies, localStorage, IndexedDB, service-worker
            registrations, fetch cache). Use if /api fetches start failing with "Load failed"
            or if the wizard reappears unexpectedly. The page reloads automatically.
          </p>
          <Button
            variant="outline"
            onClick={() => void clear()}
            disabled={status === 'clearing'}
          >
            {status === 'clearing' ? 'Clearing…' : 'Clear browsing data'}
          </Button>
        </div>
        {error && <p className="text-xs text-danger">Error: {error}</p>}
      </div>
    </Panel>
  );
}
