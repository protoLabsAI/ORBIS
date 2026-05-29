import { registerVariant } from '../registry';
import { CrystalVariant } from './CrystalVariant';
import { CRYSTAL_PRESETS } from './presets';
import { CRYSTAL_FIELDS } from './schema';
import { withSharedMoodDefaults } from '../../shared/moodDefaults';

registerVariant({
  id: 'crystal',
  name: 'Crystal',
  description: 'Faceted icosahedron with PBR transmission + iridescence.',
  Component: CrystalVariant,
  palettes: CRYSTAL_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: CRYSTAL_FIELDS,
  defaultPalette: 'Prism',
  moodDefaults: withSharedMoodDefaults({
    // Crystal: PBR knobs carry mood — iridescence and transmission
    // bloom on positive valence, cloud over when guarded; arousal
    // shimmies the surface (roughness drops, env reflection rises).
    valence: {
      iridescence: 0.10,
      transmission: 0.05,
    },
    arousal: {
      roughness: -0.06,
      envIntensity: 0.20,
      iridescence: 0.05,
    },
    guardedness: {
      transmission: -0.15,
      iridescence: -0.10,
      envIntensity: -0.20,
      roughness: 0.10,
    },
  }),
});
