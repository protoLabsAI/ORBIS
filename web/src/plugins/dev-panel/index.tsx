import { registerPlugin } from '@/sdk';
import { DevPanel } from './DevPanel';

registerPlugin({
  id: 'dev-panel',
  slots: { 'drawer-dev': DevPanel },
});
