import { registerVariant } from '../registry';
import { FluxVariant } from './FluxVariant';
import { FluxPost } from './FluxPost';
import { FLUX_FIELDS } from './schema';
import { FLUX_PRESETS } from './presets';

// Premium (paid): not in the free starter pool; `premium` gates it behind the
// customization paywall in the picker.
registerVariant({
  id: 'flux',
  name: 'Flux',
  description: 'A fluid, ever-shifting energy bloom.',
  Component: FluxVariant,
  PostEffects: FluxPost,
  palettes: FLUX_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: FLUX_FIELDS,
  defaultPalette: 'Flux',
  premium: true,
});
