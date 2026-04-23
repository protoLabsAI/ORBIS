import { registerPlugin } from '../PluginHost';
import { SettingsPanel } from './SettingsPanel';

registerPlugin({
  id: 'settings-panel',
  slots: { 'drawer-settings': SettingsPanel },
});
