import { registerVariant } from '../registry';
import { LiquidVariant } from './LiquidVariant';
import { LIQUID_PRESETS } from './presets';
import { LIQUID_FIELDS } from './schema';
import { withSharedMoodDefaults } from '../../shared/moodDefaults';

registerVariant({
  id: 'liquid',
  name: 'Liquid',
  description: 'Mercury-like surface raymarch — domain-warped height field with Phong shading and rim highlight.',
  Component: LiquidVariant,
  palettes: LIQUID_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: LIQUID_FIELDS,
  defaultPalette: 'Mercury',
  moodDefaults: withSharedMoodDefaults({
    // Liquid-specific: surface dynamics and lighting carry the mood.
    // Aroused = more agitation + brighter highlights; valence = wider
    // surface (taller bumps); guarded = quieter surface, dimmer specular.
    valence: {
      heightAmp: 0.04,
      specularIntensity: 0.10,
    },
    arousal: {
      warpAmp: 0.10,
      warpVelocity: -0.10,
      heightAmp: 0.05,
      specularIntensity: 0.10,
    },
    guardedness: {
      warpAmp: -0.10,
      heightAmp: -0.05,
      specularIntensity: -0.15,
      diffuse: -0.10,
    },
  }),
});
