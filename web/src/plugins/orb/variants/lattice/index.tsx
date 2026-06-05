import { registerVariant } from '../registry';
import { LatticeVariant } from './LatticeVariant';
import { LATTICE_PRESETS } from './presets';
import { LATTICE_FIELDS } from './schema';
import { withSharedMoodDefaults } from '../../shared/moodDefaults';

registerVariant({
  id: 'lattice',
  name: 'Lattice',
  description: 'AABB-bounded raymarch through a wrapped grid — cube silhouette with faceted lattice shells inside.',
  Component: LatticeVariant,
  palettes: LATTICE_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: LATTICE_FIELDS,
  defaultPalette: 'Glasshouse',
  premium: true,
  moodDefaults: withSharedMoodDefaults({
    // Lattice-specific: distortion + grid scale carry the mood feel.
    // Aroused = denser grid + more wobble; valence = brighter glow;
    // guarded = collapse the grid (less detail, calmer).
    valence: {
      glow: 0.10,
      gridScale: 0.05,
    },
    arousal: {
      glow: 0.10,
      distortion: 0.10,
      gridScale: 0.15,
    },
    guardedness: {
      glow: -0.15,
      gridScale: -0.10,
      distortion: -0.05,
    },
  }),
});
