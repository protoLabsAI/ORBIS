import { Panel } from '@/components/ui/panel';
import { VerbositySelector } from './VerbositySelector';

export function VoicePanel() {
  return (
    <div className="space-y-5">
      <Panel title="Agent">
        <VerbositySelector />
      </Panel>
    </div>
  );
}
