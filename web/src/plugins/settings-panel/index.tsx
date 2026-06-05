import { registerPlugin } from '../PluginHost';
import { AgentPanel } from './AgentPanel';
import { SystemPanel } from './SystemPanel';
import { VoicePanel } from './VoicePanel';

// Three logical drawer tabs: Voice (audio I/O), Brain (LLM + delegates +
// replies), and Settings (access + setup + diagnostics + about). Personality
// lives in the Dev tab; the Quick landing tab is its own plugin.
registerPlugin({
  id: 'settings-panel',
  slots: {
    'drawer-voice': VoicePanel,
    'drawer-brain': AgentPanel,
    'drawer-settings': SystemPanel,
  },
});
