import { Diagnostics } from './Diagnostics';
import { DelegatesSettings } from './DelegatesSettings';
import { LLMSettings } from './LLMSettings';
import { MicSettings } from './MicSettings';
import { STTSettings } from './STTSettings';
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
      <STTSettings />
      <DelegatesSettings />
      <MicSettings />
      <Diagnostics />
    </div>
  );
}
