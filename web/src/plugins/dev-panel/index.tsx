import { registerPlugin } from '../PluginHost';
import { DevPanel } from './DevPanel';

registerPlugin({
  id: 'dev-panel',
  slots: { 'drawer-dev': DevPanel },
});
