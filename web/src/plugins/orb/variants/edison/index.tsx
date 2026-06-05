import { registerVariant } from '../registry';
import { BETA_ORBS } from '../betaOrbs';
import { EdisonVariant } from './EdisonVariant';
import { EdisonPost } from './EdisonPost';
import { EDISON_FIELDS } from './schema';
import { EDISON_PRESETS } from './presets';

// Premium + beta-gated: registers only when BETA_ORBS is on, and `premium`
// marks it for the customization paywall in the picker. Not added to the free
// starter pool, so free/beta users never see it.
if (BETA_ORBS) {
  registerVariant({
    id: 'edison',
    name: 'Edison',
    description: 'A living filament bulb — writhing strands of light in glass.',
    Component: EdisonVariant,
    PostEffects: EdisonPost,
    palettes: EDISON_PRESETS as unknown as Record<string, Record<string, unknown>>,
    fields: EDISON_FIELDS,
    defaultPalette: 'Edison',
    premium: true,
  });
}
