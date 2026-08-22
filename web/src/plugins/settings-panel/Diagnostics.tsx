import { useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { api, type DiagnosticsReport } from '@/lib/api';

/**
 * Diagnostic actions surfaced to the user when something feels off.
 *
 * - "Copy diagnostics" — fetches GET /api/diagnostics (versions, runtime
 *   shape, metrics, and the config with provider secrets already redacted
 *   server-side) and copies it to the clipboard as a markdown block ready to
 *   paste into a GitHub issue (#488). The raw log is not inlined (it can
 *   carry transcripts) — its path is included so the user attaches it via the
 *   reveal button below.
 * - "Reveal logs in Finder" — invokes the `reveal_logs` Tauri IPC, which
 *   opens the app log dir (sidecar.log + the Rust/tauri-plugin-log output)
 *   so a user can attach logs to a bug report.
 * - "Export diagnostics…" — invokes the `export_diagnostics` Tauri IPC, which
 *   zips sidecar.log + the /healthz snapshot + app version + the config with
 *   secrets redacted (Rust-side, before anything leaves the box) to
 *   `~/Desktop/orbis-diagnostics-<timestamp>.zip` — the one-file attachment
 *   for a bug report (#488 export slice).
 * - "Clear browsing data" — invokes `clear_browsing_data`, which calls
 *   `Webview::clear_all_browsing_data()` to wipe cookies, IndexedDB,
 *   localStorage, service-worker registrations, and the fetch cache. Use
 *   when the wizard or any /api/* fetch misbehaves after a sidecar version
 *   bump (stale SW / localStorage). The offline equivalent is
 *   `scripts/nuke-and-rebuild.sh`; this in-app button is the lighter-touch
 *   runtime fix that doesn't require closing the app.
 */

/** Render the diagnostics bundle as a GitHub-issue-ready markdown block. */
function formatDiagnostics(d: DiagnosticsReport): string {
  const app = d.app ?? ({} as DiagnosticsReport['app']);
  const lines = [
    '### ORBIS diagnostics',
    '',
    `- **App** ${app.version ?? '?'} · Python ${app.python ?? '?'} · ${app.platform ?? '?'} (${app.machine ?? '?'})`,
  ];
  if (d.generated_at) lines.push(`- **Captured** ${d.generated_at}`);
  lines.push(
    '',
    '**Runtime**',
    '```json',
    JSON.stringify(d.runtime ?? {}, null, 2),
    '```',
    '',
    '**Metrics**',
    '```json',
    JSON.stringify(d.metrics ?? {}, null, 2),
    '```',
    '',
    '**Config** (secrets redacted)',
    '```json',
    JSON.stringify(d.config ?? {}, null, 2),
    '```',
    '',
    `**Logs** \`${d.logs?.path ?? ''}\``,
  );
  if (d.logs?.note) lines.push(`_${d.logs.note}_`);
  return lines.join('\n');
}

export function Diagnostics() {
  const [status, setStatus] = useState<'idle' | 'clearing' | 'cleared' | 'error'>('idle');
  const [copy, setCopy] = useState<'idle' | 'copying' | 'copied'>('idle');
  const [exportState, setExportState] = useState<'idle' | 'exporting' | 'done'>('idle');
  const [exportPath, setExportPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetch the redacted bundle and drop it on the clipboard as markdown so the
  // user can paste it straight into a bug report (#488).
  const copyDiagnostics = async () => {
    setCopy('copying');
    setError(null);
    try {
      const report = await api.diagnostics();
      await navigator.clipboard.writeText(formatDiagnostics(report));
      setCopy('copied');
      setTimeout(() => setCopy('idle'), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setCopy('idle');
      setStatus('error');
    }
  };

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

  // Bundle sidecar.log + /healthz + app version + redacted config into a zip
  // on the Desktop — the attach-one-file complement to "Copy diagnostics".
  // Redaction happens Rust-side before the zip is written (#488).
  const exportDiagnostics = async () => {
    setExportState('exporting');
    setError(null);
    try {
      const path = await invoke<string>('export_diagnostics');
      setExportPath(path);
      setExportState('done');
    } catch (e) {
      // `error` renders the failure; `status` belongs to the unrelated
      // "Clear browsing data" state machine — don't cross-contaminate it.
      setError(e instanceof Error ? e.message : String(e));
      setExportState('idle');
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
            Copy your setup — app/OS versions, runtime state, metrics, and config with
            secrets redacted — as a markdown block to paste into a bug report. Then use
            "Reveal logs in Finder" below to attach the log file.
          </p>
          <Button
            variant="outline"
            onClick={() => void copyDiagnostics()}
            disabled={copy === 'copying'}
          >
            {copy === 'copying' ? 'Copying…' : copy === 'copied' ? 'Copied ✓' : 'Copy diagnostics'}
          </Button>
        </div>
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
            Save everything as one zip on your Desktop — the sidecar log, a /healthz
            snapshot, app version, and your config with secrets redacted. Attach it to
            a bug report instead of gathering the pieces by hand.
          </p>
          <Button
            variant="outline"
            onClick={() => void exportDiagnostics()}
            disabled={exportState === 'exporting'}
          >
            {exportState === 'exporting' ? 'Exporting…' : 'Export diagnostics…'}
          </Button>
          {exportState === 'done' && exportPath && (
            <p className="text-xs text-fg-muted break-all">Saved to {exportPath}</p>
          )}
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
