import { registerVariant } from '../registry';
import { FractalVariant } from './FractalVariant';
import { FRACTAL_PRESETS } from './presets';
import { FRACTAL_FIELDS } from './schema';
import { withSharedMoodDefaults } from '../../shared/moodDefaults';

registerVariant({
  id: 'fractal',
  name: 'Fractal',
  description: 'Ray-marched volumetric fractal with atmosphere shell.',
  Component: FractalVariant,
  palettes: FRACTAL_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: FRACTAL_FIELDS,
  defaultPalette: 'Aurora',
  moodDefaults: withSharedMoodDefaults({
    // Fractal: more iterations and smoother folds when activated; less
    // when guarded. The internal anim speed pumps with arousal.
    arousal: {
      fractalIters: 0.8,        // up to +1 full iteration at max arousal
      internalAnim: 0.10,
      fractalScale: 0.04,
    },
    guardedness: {
      fractalIters: -0.5,
      smoothness: -0.008,        // sharper edges when closed up
      internalAnim: -0.06,
    },
    valence: {
      fractalScale: 0.05,        // gentler organic spread on positive valence
    },
  }),
});
