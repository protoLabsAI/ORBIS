import { registerVariant } from '../registry';
import { TetraVariant } from './TetraVariant';
import { TETRA_PRESETS } from './presets';
import { TETRA_FIELDS } from './schema';

registerVariant({
  id: 'tetra',
  name: 'Tetra',
  description: 'Tetrahedron-bound spherical-inversion fractal — sharp silhouette, curling inner engine.',
  Component: TetraVariant,
  palettes: TETRA_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: TETRA_FIELDS,
  defaultPalette: 'Drift',
});
