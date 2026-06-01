import { useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';

/**
 * Diagnostic actions surfaced to the user when something feels off.
 *
 * Today: a single button — "Clear browsing data" — invokes the
 * `clear_browsing_data` Tauri IPC, which calls
 * `Webview::clear_all_browsing_data()` to wipe cookies, IndexedDB,
 * localStorage, service-worker registrations, and the fetch cache.
 *
 * Use when the wizard or any /api/* fetch starts misbehaving after a
 * sidecar version bump — the previous SW or stale localStorage is
 * usually the culprit. The offline equivalent is the
 * `scripts/nuke-and-rebuild.sh` script which also wipes the file-
 * system-level WebKit caches; this in-app button is the lighter-touch
 * runtime fix that doesn't require closing the app.
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

  return (
    <Panel title="Diagnostics">
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
        {error && <p className="text-xs text-danger">Error: {error}</p>}
      </div>
    </Panel>
  );
}
