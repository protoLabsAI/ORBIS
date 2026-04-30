import { Panel } from '@/components/ui/panel';

/**
 * Developer drawer tab — feature flags + power-user knobs. Empty
 * scaffold for now; flags get wired in here as they appear (e.g.
 * micro-ack toggle, echo-guard threshold slider, STT backend swap).
 */
export function DevPanel() {
  return (
    <div className="space-y-4">
      <Panel title="Feature flags">
        <p className="text-xs text-zinc-500">
          Runtime flags will live here. None defined yet.
        </p>
      </Panel>
    </div>
  );
}
