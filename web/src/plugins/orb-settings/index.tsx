import { registerPlugin } from '../PluginHost';
import { OrbSettingsPanel } from './OrbSettingsPanel';

registerPlugin({
  id: 'orb-settings',
  slots: { 'drawer-orb': OrbSettingsPanel },
});
