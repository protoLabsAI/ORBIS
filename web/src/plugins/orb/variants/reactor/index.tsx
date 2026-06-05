import { registerVariant } from '../registry';
import { BETA_ORBS } from '../betaOrbs';
import { ReactorVariant } from './ReactorVariant';
import { ReactorPost } from './ReactorPost';
import { REACTOR_FIELDS } from './schema';
import { REACTOR_PRESETS } from './presets';

// Premium + beta-gated (see edison/index.tsx). Not in the free starter pool.
if (BETA_ORBS) {
  registerVariant({
    id: 'reactor',
    name: 'Reactor',
    description: 'A pulsing kaleidoscopic energy core, caged in glass.',
    Component: ReactorVariant,
    PostEffects: ReactorPost,
    palettes: REACTOR_PRESETS as unknown as Record<string, Record<string, unknown>>,
    fields: REACTOR_FIELDS,
    defaultPalette: 'Reactor',
    premium: true,
  });
}
