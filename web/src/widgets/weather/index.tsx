import { CloudSun } from 'lucide-react';
import { registerWidget } from '@/sdk';
import { Weather } from './Weather';

// Self-registering widget — picked up by the eager glob in ../index.ts.
registerWidget({
  id: 'weather',
  title: 'Weather',
  icon: CloudSun,
  klass: 'glance',
  render: Weather,
});
