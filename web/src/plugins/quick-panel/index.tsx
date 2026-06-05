import { registerPlugin } from '../PluginHost';
import { QuickPanel } from './QuickPanel';

registerPlugin({
  id: 'quick-panel',
  slots: { 'drawer-quick': QuickPanel },
});
