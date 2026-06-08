import { registerVariant } from '@/sdk';
import { ParticlesVariant } from './ParticlesVariant';
import { PARTICLES_PRESETS } from './presets';
import { PARTICLES_FIELDS } from './schema';
import { withSharedMoodDefaults } from '../../shared/moodDefaults';

registerVariant({
  id: 'particles',
  name: 'Particles',
  description: 'Fibonacci-lattice sphere of small icosahedra, audio-reactive.',
  Component: ParticlesVariant,
  palettes: PARTICLES_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: PARTICLES_FIELDS,
  defaultPalette: 'Constellation',
  moodDefaults: withSharedMoodDefaults({
    // Particles: jitter and hueSpread carry the energy. Aroused = more
    // chaos, more colour variance. Guarded = tight lattice, low spread.
    arousal: {
      jitter: 0.08,
      hueSpread: 15,
      particleSize: 0.005,
    },
    valence: {
      hueSpread: 8,
      particleSize: 0.003,
    },
    guardedness: {
      jitter: -0.06,
      hueSpread: -10,
      radius: -0.05,
    },
  }),
});
