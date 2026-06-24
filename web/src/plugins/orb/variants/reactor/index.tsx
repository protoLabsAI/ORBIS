import { registerVariant } from '@/sdk';
import { ReactorVariant } from './ReactorVariant';
import { ReactorPost } from './ReactorPost';
import { REACTOR_FIELDS } from './schema';
import { REACTOR_PRESETS } from './presets';

// Not a default first-run starter (`premium: true`) — free and fully usable
// like every variant, just not offered during setup.
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
