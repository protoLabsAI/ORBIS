import { CollapsiblePanelProvider } from '@/components/ui/panel';
import { MicSettings } from './MicSettings';
import { STTSettings } from './STTSettings';
import { TTSSettings } from './TTSSettings';

/**
 * Voice tab — the full audio I/O pipeline: microphone (input device +
 * permission), speech-to-text, and text-to-speech voice. Grouped because
 * they're the one cohesive "how the orb hears and speaks" surface.
 */
export function VoicePanel() {
  return (
    <CollapsiblePanelProvider storageKey="orbis.voicePanel">
      <div className="space-y-5">
        <MicSettings />
        <STTSettings />
        <TTSSettings />
      </div>
    </CollapsiblePanelProvider>
  );
}
