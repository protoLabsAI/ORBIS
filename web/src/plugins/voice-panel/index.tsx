import { registerPlugin } from '../PluginHost';
import { VoicePanel } from './VoicePanel';

registerPlugin({
  id: 'voice-panel',
  slots: { 'drawer-voice': VoicePanel },
});
