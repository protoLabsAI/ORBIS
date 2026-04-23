import { registerPlugin } from '../PluginHost';
import { OrbStage } from './OrbStage';
import { loadOrbOverrides } from './configDriver';

registerPlugin({
  id: 'orb',
  slots: { stage: OrbStage },
});

// Fire once at plugin load. Idempotent; if it fails the orb just
// renders with empty override maps (no-op composition) until the user
// saves something via the drawer.
void loadOrbOverrides();
