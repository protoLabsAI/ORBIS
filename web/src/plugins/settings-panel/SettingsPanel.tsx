import { Button } from '@/components/ui/button';
import { CollapsiblePanelProvider, Panel } from '@/components/ui/panel';
import { devModeStore, useDevMode } from '@/shared/devMode';
import { Diagnostics } from './Diagnostics';
import { DelegatesSettings } from './DelegatesSettings';
import { LLMSettings } from './LLMSettings';
import { MicSettings } from './MicSettings';
import { ApiKeyField } from './ApiKeyField';
import { PersonalityPanel } from './PersonalityPanel';
import { STTSettings } from './STTSettings';
import { TTSSettings } from './TTSSettings';
import { VerbositySelector } from './VerbositySelector';

/**
 * Runtime settings — provider config, native audio, agent behavior,
 * access, diagnostics, and developer tools in one drawer tab. Saves land
 * in config/orbis.yaml and reload the persona server-side, so the next
 * voice turn picks up the change.
 */
export function SettingsPanel() {
  const devMode = useDevMode();

  return (
    <CollapsiblePanelProvider storageKey="orbis.settingsPanel">
      <div className="space-y-5">
        <MicSettings />
        <STTSettings />
        <LLMSettings />
        <TTSSettings />
        <Panel title="Agent">
          <VerbositySelector />
        </Panel>
        <DelegatesSettings />
        <PersonalityPanel />
        <Panel title="Access">
          <ApiKeyField />
        </Panel>
        <Diagnostics />
        <Panel title="Developer">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs text-zinc-300">Developer mode</div>
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
