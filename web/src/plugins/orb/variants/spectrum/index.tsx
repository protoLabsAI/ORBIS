import { registerVariant } from '@/sdk';
import { SpectrumVariant } from './SpectrumVariant';
import { SPECTRUM_PRESETS } from './presets';
import { SPECTRUM_FIELDS } from './schema';
import { withSharedMoodDefaults } from '../../shared/moodDefaults';

registerVariant({
  id: 'spectrum',
  name: 'Spectrum',
  description: 'Rainbow volumetric orb — two smooth-blended SDFs, nested turbulence, cosine-palette emission.',
  Component: SpectrumVariant,
  palettes: SPECTRUM_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: SPECTRUM_FIELDS,
  defaultPalette: 'Rainbow',
  premium: true,
  moodDefaults: withSharedMoodDefaults({
    // Spectrum-specific: the soft fade boundary + smoothing carry the
    // mood feel. Aroused = sharper boundary + brighter glow; valence =
    // wider halo (more open feel); guarded = compressed halo + softer
    // boundary (more closed-in).
    valence: {
      glow: 0.10,
      fadeOuter: 0.08,
    },
    arousal: {
      glow: 0.15,
      smoothing: -0.15,
    },
    guardedness: {
      glow: -0.15,
      fadeOuter: -0.10,
      smoothing: 0.20,
    },
  }),
});
