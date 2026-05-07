import { pluginRegistry } from '@/plugins/registry';
import { DevPanel } from './DevPanel';

pluginRegistry.register({
  id: 'dev-panel',
  slots: { 'drawer-dev': DevPanel },
});
