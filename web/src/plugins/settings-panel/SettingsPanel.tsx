import { Panel } from '@/components/ui/panel';
import { LLMSettings } from './LLMSettings';
import { MicSettings } from './MicSettings';
import { TTSSettings } from './TTSSettings';

/**
 * Runtime settings — swap provider / voice without re-running the setup
 * wizard. Saves land in config/orbis.yaml and reload the persona
 * server-side, so the next voice turn picks up the change.
 */
export function SettingsPanel() {
  return (
    <div className="space-y-5">
      <LLMSettings />
      <TTSSettings />
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
