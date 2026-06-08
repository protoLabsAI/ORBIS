import { registerPlugin } from '@/sdk';
import { QuickPanel } from './QuickPanel';

registerPlugin({
  id: 'quick-panel',
  slots: { 'drawer-quick': QuickPanel },
});
