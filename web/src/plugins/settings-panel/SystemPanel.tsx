import { Button } from '@/components/ui/button';
import { CollapsiblePanelProvider, Panel } from '@/components/ui/panel';
import { api } from '@/lib/api';
import { DEVTOOLS_ENABLED, devModeStore, useDevMode } from '@/shared/devMode';
import { ApiKeyField } from './ApiKeyField';
import { Diagnostics } from './Diagnostics';
import { AboutPanel } from './AboutPanel';

/**
 * Settings tab — owner access, setup, diagnostics, and about. The developer-mode
 * toggle (which reveals the Dev tab) is demoted to a quiet footer line rather
 * than a section of its own, so the tab reads as four real settings instead of
 * a grab-bag.
 */
export function SystemPanel() {
  const devMode = useDevMode();

  return (
    <CollapsiblePanelProvider storageKey="orbis.systemPanel">
      <div className="space-y-5">
        <Panel title="Access">
          <ApiKeyField />
        </Panel>

        <Panel title="Setup">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-xs text-fg-body">Re-run setup</div>
              <div className="mt-0.5 text-helper text-fg-muted">
                Replay the first-run wizard — mic, voice models, names, orb.
              </div>
            </div>
            <Button
              size="sm"
              variant="secondary"
              onClick={async () => {
                try {
                  localStorage.removeItem('orbis.setupComplete');
                } catch {
                  // best-effort — fall through regardless
                }
                // Clear the durable flag too, else the config-gated wizard
                // stays hidden and "Re-run" would no-op.
                try {
                  await api.putConfig({ setup: { complete: false } });
                } catch {
                  // best-effort — reload anyway
                }
                window.location.reload();
              }}
            >
              Re-run
            </Button>
          </div>
        </Panel>

        <Diagnostics />

        <AboutPanel />

        {/* Developer mode is a build-time capability (VITE_ORBIS_DEVTOOLS), not
            a user setting — never offered in a shipped build, so the Dev/Orb
            tabs + premium-orb picker can't be reached for free. */}
        {DEVTOOLS_ENABLED && (
          <div className="flex items-center justify-between gap-3 border-t border-edge pt-3">
            <span className="text-helper text-fg-subtle">Developer mode</span>
            <button
              type="button"
              onClick={() => devModeStore.toggle()}
              aria-pressed={devMode}
              className="text-helper text-fg-subtle hover:text-fg-body transition-colors"
            >
              {devMode ? 'On' : 'Off'}
            </button>
          </div>
        )}
      </div>
    </CollapsiblePanelProvider>
  );
}
