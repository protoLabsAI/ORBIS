import { registerPlugin } from '@/sdk';
import { StatusPill } from './StatusPill';

registerPlugin({
  id: 'status-pill',
  slots: { 'overlay-bottom': StatusPill },
});
