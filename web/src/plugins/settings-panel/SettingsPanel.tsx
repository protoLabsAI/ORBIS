import { CollapsiblePanelProvider, Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { devModeStore, useDevMode } from '@/shared/devMode';
import { DelegatesSettings } from './DelegatesSettings';
import { LLMSettings } from './LLMSettings';
import { MicSettings } from './MicSettings';
import { PersonalityPanel } from './PersonalityPanel';
import { STTSettings } from './STTSettings';
import { TTSSettings } from './TTSSettings';
import { VerbositySelector } from './VerbositySelector';

/**
 * The single settings drawer — provider config, voice + behaviour
 * knobs, observability, and the dev-mode toggle. Sections are ordered
 * roughly along the audio path (mic → STT → LLM → voice/TTS), then
 * agent behaviour, then observability, then dev.
 *
 * Wrapped in ``CollapsiblePanelProvider`` so every nested ``<Panel>``
 * (including ones rendered by child components like LLMSettings)
 * becomes a click-to-toggle accordion section. Open/closed state is
 * persisted per section title in localStorage under the
 * ``orbis.settings.panel`` namespace so the user's preferred layout
 * survives reloads + restarts.
 */
export function SettingsPanel() {
  const devMode = useDevMode();
  return (
    <CollapsiblePanelProvider storageKey="orbis.settings.panel">
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
        <Panel title="Developer">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-zinc-300">Developer mode</div>
              <div className="text-[10px] text-zinc-500 mt-0.5">
                Reveals the Dev drawer tab (feature flags + event log).
              </div>
            </div>
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
