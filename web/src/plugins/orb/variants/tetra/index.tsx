import { registerVariant } from '@/sdk';
import { TetraVariant } from './TetraVariant';
import { TETRA_PRESETS } from './presets';
import { TETRA_FIELDS } from './schema';
import { withSharedMoodDefaults } from '../../shared/moodDefaults';

registerVariant({
  id: 'tetra',
  name: 'Tetra',
  description: 'Tetrahedron-bound spherical-inversion fractal — sharp silhouette, curling inner engine.',
  Component: TetraVariant,
  palettes: TETRA_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: TETRA_FIELDS,
  defaultPalette: 'Drift',
  premium: true,
  moodDefaults: withSharedMoodDefaults({
    // Tetra-specific: glow intensity and base mod the volumetric
    // accumulator directly, so they're the most "you can feel it" knobs.
    valence: {
      glowIntensity: 0.010,
      glowBase: 0.06,
    },
    arousal: {
      glowIntensity: 0.008,
      internalAnim: 0.06,
    },
    guardedness: {
      glowIntensity: -0.012,
      internalAnim: -0.04,
      shapeSize: 0.15,  // tighter tetrahedron at high guardedness
    },
  }),
});
