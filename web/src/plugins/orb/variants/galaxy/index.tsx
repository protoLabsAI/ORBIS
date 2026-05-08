import { registerVariant } from '../registry';
import { GalaxyVariant } from './GalaxyVariant';
import { GALAXY_PRESETS } from './presets';
import { GALAXY_FIELDS } from './schema';
import { withSharedMoodDefaults } from '../../shared/moodDefaults';

registerVariant({
  id: 'galaxy',
  name: 'Galaxy',
  description: 'Three-layer composition — Fresnel shell, domain-warped plasma core, dust-particle field.',
  Component: GalaxyVariant,
  palettes: GALAXY_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: GALAXY_FIELDS,
  defaultPalette: 'Andromeda',
  moodDefaults: withSharedMoodDefaults({
    // Galaxy-specific: plasma brightness + voids carry mood. Aroused
    // = brighter, denser plasma + more voice-tint bleed; valence =
    // brighter cores; guarded = darker, more void.
    valence: {
      plasmaBrightness: 0.10,
      shellOpacity: 0.05,
    },
    arousal: {
      plasmaBrightness: 0.15,
      voiceMix: 0.10,
      voidThreshold: -0.02,
    },
    guardedness: {
      plasmaBrightness: -0.20,
      voidThreshold: 0.05,
      shellOpacity: -0.10,
    },
  }),
});
