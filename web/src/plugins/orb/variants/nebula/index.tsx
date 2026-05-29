import { registerVariant } from '../registry';
import { NebulaVariant } from './NebulaVariant';
import { NEBULA_PRESETS } from './presets';
import { NEBULA_FIELDS } from './schema';
import { withSharedMoodDefaults } from '../../shared/moodDefaults';

registerVariant({
  id: 'nebula',
  name: 'Nebula',
  description: 'Noise-based volumetric cloud — raymarched FBM with domain warp.',
  Component: NebulaVariant,
  palettes: NEBULA_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: NEBULA_FIELDS,
  defaultPalette: 'Andromeda',
  moodDefaults: withSharedMoodDefaults({
    // Nebula: cloudiness + drift carry the mood feel — clouds churn
    // faster when aroused, thicken with positive valence, dissolve
    // when guarded.
    arousal: {
      cloudiness: 0.20,
      drift: 0.30,
    },
    valence: {
      cloudiness: 0.10,
      softness: 0.04,
    },
    guardedness: {
      cloudiness: -0.20,
      drift: -0.20,
      softness: -0.05,
    },
  }),
});
