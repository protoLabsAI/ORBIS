import { registerPlugin } from '../PluginHost';
import { StatusPill } from './StatusPill';

registerPlugin({
  id: 'status-pill',
  slots: { 'overlay-bottom': StatusPill },
});
