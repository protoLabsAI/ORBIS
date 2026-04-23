import { Panel } from '@/components/ui/panel';
import { ApiKeyField } from './ApiKeyField';
import { VerbositySelector } from './VerbositySelector';

export function VoicePanel() {
  return (
    <div className="space-y-5">
      <Panel title="Agent">
        <VerbositySelector />
      </Panel>
      <Panel title="Access">
        <ApiKeyField />
      </Panel>
    </div>
  );
}
