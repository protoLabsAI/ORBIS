import { CollapsiblePanelProvider, Panel } from '@/components/ui/panel';
import { LLMSettings } from './LLMSettings';
import { DelegatesSettings } from './DelegatesSettings';
import { RemindersPanel } from './RemindersPanel';
import { VerbositySelector } from './VerbositySelector';

/**
 * Agent tab — how the orb thinks, routes, and acts: the brain LLM, the
 * sub-agents it delegates to, its scheduled reminders, and how talkative it
 * is. (LLM + Delegates both configure model endpoints; keeping them adjacent
 * makes that relationship legible.)
 */
export function AgentPanel() {
  return (
    <CollapsiblePanelProvider storageKey="orbis.agentPanel">
      <div className="space-y-5">
        <LLMSettings />
        <DelegatesSettings />
        <RemindersPanel />
        <Panel title="Behavior">
          <VerbositySelector />
        </Panel>
      </div>
    </CollapsiblePanelProvider>
  );
}
