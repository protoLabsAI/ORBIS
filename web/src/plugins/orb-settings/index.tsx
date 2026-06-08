import { registerPlugin } from '@/sdk';
import { OrbSettingsPanel } from './OrbSettingsPanel';

registerPlugin({
  id: 'orb-settings',
  slots: { 'drawer-orb': OrbSettingsPanel },
});
