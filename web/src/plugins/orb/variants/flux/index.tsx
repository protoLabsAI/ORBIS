import { registerVariant } from '@/sdk';
import { FluxVariant } from './FluxVariant';
import { FluxPost } from './FluxPost';
import { FLUX_FIELDS } from './schema';
import { FLUX_PRESETS } from './presets';

// Not a default first-run starter (`premium: true`) — free and fully usable
// like every variant, just not offered during setup.
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
