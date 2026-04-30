import { Panel } from '@/components/ui/panel';
import { LogsPanel } from '@/plugins/logs-panel/LogsPanel';

/**
 * Developer drawer tab — feature flags + the live event log. Logs sit
 * inside a collapsed-by-default <details> so the panel doesn't scroll
 * past the flags by default, but devs can pop it open to tail the
 * client/server frame stream during a session.
 */
export function DevPanel() {
  return (
    <div className="space-y-4">
      <Panel title="Feature flags">
        <p className="text-xs text-zinc-500">
          Runtime flags will live here. None defined yet.
        </p>
      </Panel>
      {/* Native <details> — semantic, accessible, zero-deps disclosure.
          Open class on details:open[summary]:after lets us flip the
          chevron without state. */}
      <details className="group rounded-lg border border-zinc-800 bg-zinc-900/30">
        <summary
          className="cursor-pointer select-none px-3 py-2 text-xs font-mono uppercase tracking-wider text-zinc-400 hover:text-zinc-200 flex items-center justify-between"
        >
          <span>Event log</span>
          <span className="text-zinc-600 group-open:rotate-90 transition-transform">›</span>
        </summary>
        <div className="px-3 pb-3 pt-1">
          <LogsPanel />
        </div>
      </details>
    </div>
  );
}
