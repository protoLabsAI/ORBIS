import { Panel } from '@/components/ui/panel';
import { SkillSelector } from './SkillSelector';
import { VerbositySelector } from './VerbositySelector';
import { VoiceCloneForm } from './VoiceCloneForm';

export function VoicePanel() {
  return (
    <div className="space-y-5">
      <Panel title="Agent">
        <SkillSelector />
        <VerbositySelector />
      </Panel>
      <VoiceCloneForm />
    </div>
  );
}
