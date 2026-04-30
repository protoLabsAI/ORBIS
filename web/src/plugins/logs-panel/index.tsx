import { pluginRegistry } from '@/plugins/registry';
import { LogsPanel } from './LogsPanel';

pluginRegistry.register({
  id: 'logs-panel',
  slots: { 'drawer-logs': LogsPanel },
});

export { LogsCollector } from './LogsCollector';
