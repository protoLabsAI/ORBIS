import { registerVariant } from '../registry';
import { BETA_ORBS } from '../betaOrbs';
import { FluxVariant } from './FluxVariant';
import { FluxPost } from './FluxPost';
import { FLUX_FIELDS } from './schema';
import { FLUX_PRESETS } from './presets';

// Premium + beta-gated (see edison/index.tsx). Not in the free starter pool.
if (BETA_ORBS) {
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
}
