import { Panel } from '@/components/ui/panel';
import { LogsPanel } from '@/plugins/logs-panel/LogsPanel';

/**
 * Developer drawer tab — feature flags and the live native event log. The
 * personality/mood observability panel was pulled for the beta along with the
 * paused emotional-companion layer (PersonalityPanel is kept in-tree for an
 * easy re-enable). The premium-orbs picker (BetaOrbsPanel) was removed
 * outright; preview the non-starter variants via `vite dev` and the
 * variant registry. Logs sit inside a collapsed-by-default
 * <details> so the panel doesn't scroll past everything by default.
 */
export function DevPanel() {
  return (
    <div className="space-y-4">
      <Panel title="Feature flags">
        <p className="text-xs text-fg-subtle">
          Runtime flags will live here. None defined yet.
        </p>
      </Panel>
      <details className="group rounded-lg border border-edge bg-raised/30">
        <summary className="flex cursor-pointer select-none items-center justify-between px-3 py-2 font-mono text-xs uppercase tracking-wider text-fg-muted hover:text-fg-body">
          <span>Event log</span>
          <span className="text-fg-faint transition-transform group-open:rotate-90">›</span>
        </summary>
        <div className="px-3 pb-3 pt-1">
          <LogsPanel />
        </div>
      </details>
    </div>
  );
}
