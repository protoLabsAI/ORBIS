import { registerVariant } from '../registry';
import { ParticlesVariant } from './ParticlesVariant';
import { PARTICLES_PRESETS } from './presets';
import { PARTICLES_FIELDS } from './schema';

registerVariant({
  id: 'particles',
  name: 'Particles',
  description: 'Fibonacci-lattice sphere of small icosahedra, audio-reactive.',
  Component: ParticlesVariant,
  palettes: PARTICLES_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: PARTICLES_FIELDS,
  defaultPalette: 'Constellation',
});
