import { registerVariant } from '../registry';
import { BETA_ORBS } from '../betaOrbs';
import { DiscoVariant } from './DiscoVariant';
import { DiscoPost } from './DiscoPost';
import { DISCO_FIELDS } from './schema';
import { DISCO_PRESETS } from './presets';

// Premium + beta-gated (see edison/index.tsx). Not in the free starter pool.
if (BETA_ORBS) {
  registerVariant({
    id: 'disco',
    name: 'Disco',
    description: 'A kaleidoscopic fractal caged in glass — a living disco ball.',
    Component: DiscoVariant,
    PostEffects: DiscoPost,
    palettes: DISCO_PRESETS as unknown as Record<string, Record<string, unknown>>,
    fields: DISCO_FIELDS,
    defaultPalette: 'Disco',
    premium: true,
  });
}
