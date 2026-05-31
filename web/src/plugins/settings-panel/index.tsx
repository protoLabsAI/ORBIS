import { registerPlugin } from '../PluginHost';
import { AgentPanel } from './AgentPanel';
import { SystemPanel } from './SystemPanel';
import { VoicePanel } from './VoicePanel';

// The former single "Settings" tab is now three logical drawer tabs:
// Voice (audio I/O), Agent (brain + delegates + reminders + behavior), and
// System (access + diagnostics + dev toggle). Personality moved to the Dev tab.
registerPlugin({
  id: 'settings-panel',
  slots: {
    'drawer-voice': VoicePanel,
    'drawer-agent': AgentPanel,
    'drawer-system': SystemPanel,
  },
});
