import { Network } from 'lucide-react';
import { registerWidget } from '@/sdk';
import { Delegations } from './Delegations';

// Self-registering widget — picked up by the eager glob in ../index.ts.
registerWidget({
  id: 'delegations',
  title: 'Delegations',
  icon: Network,
  klass: 'agent',
  render: Delegations,
});
