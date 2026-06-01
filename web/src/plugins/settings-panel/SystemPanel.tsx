import { Button } from '@/components/ui/button';
import { CollapsiblePanelProvider, Panel } from '@/components/ui/panel';
import { devModeStore, useDevMode } from '@/shared/devMode';
import { ApiKeyField } from './ApiKeyField';
import { Diagnostics } from './Diagnostics';

/**
 * System tab — operational/admin: owner API key (access), the WKWebView
 * cache-clear diagnostic, and the developer-mode toggle that reveals the Dev
 * tab. Not part of normal day-to-day use.
 */
export function SystemPanel() {
  const devMode = useDevMode();

  return (
    <CollapsiblePanelProvider storageKey="orbis.systemPanel">
      <div className="space-y-5">
        <Panel title="Access">
          <ApiKeyField />
        </Panel>
        <Diagnostics />
        <Panel title="Developer">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs text-fg-body">Developer mode</div>
            <Button
              size="sm"
              variant={devMode ? 'default' : 'secondary'}
              onClick={() => devModeStore.toggle()}
            >
              {devMode ? 'On' : 'Off'}
            </Button>
          </div>
        </Panel>
      </div>
    </CollapsiblePanelProvider>
  );
}
