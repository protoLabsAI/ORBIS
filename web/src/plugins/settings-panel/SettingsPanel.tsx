import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { devModeStore, useDevMode } from '@/shared/devMode';
import { LLMSettings } from './LLMSettings';
import { MicSettings } from './MicSettings';
import { STTSettings } from './STTSettings';

/**
 * Runtime infrastructure settings — LLM endpoint, mic device, STT.
 * Voice/TTS lives in the Voice drawer alongside personality + verbosity
 * since it's about how the orb sounds, not which provider it routes to.
 */
export function SettingsPanel() {
  const devMode = useDevMode();
  return (
    <div className="space-y-5">
      <LLMSettings />
      <MicSettings />
      <STTSettings />
      <Panel title="Developer">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-xs text-zinc-300">Developer mode</div>
            <div className="text-[10px] text-zinc-500 mt-0.5">
              Reveals the Dev + Logs drawer tabs.
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
  );
}
