import { Panel } from '@/components/ui/panel';
import { LLMSettings } from './LLMSettings';
import { MicSettings } from './MicSettings';

/**
 * Runtime infrastructure settings — LLM endpoint, mic device, STT.
 * Voice/TTS lives in the Voice drawer alongside personality + verbosity
 * since it's about how the orb sounds, not which provider it routes to.
 */
export function SettingsPanel() {
  return (
    <div className="space-y-5">
      <LLMSettings />
      <MicSettings />
      <Panel title="STT">
        <p className="text-xs text-zinc-500">
          Speech-to-text is configured via environment variables today —
          <code className="mx-1">STT_BACKEND</code>,
          <code className="mx-1">WHISPER_MODEL</code>,
          <code className="mx-1">STT_URL</code>,
          <code className="mx-1">STT_API_KEY</code>. See
          <code className="mx-1">.env.example</code>. UI pending.
        </p>
      </Panel>
    </div>
  );
}
