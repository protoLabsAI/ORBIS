// TEMPORARILY DISABLED 2026-06-25 — Edison needs more work before it's
// shippable. Variant auto-discovery globs `variants/*/index.tsx` (see
// ../index.ts), so naming this file `index.disabled.tsx` keeps it out of
// the registry and out of the bundle while preserving all the code.
// To re-enable: rename this file back to `index.tsx`.
import { registerVariant } from '@/sdk';
import { EdisonVariant } from './EdisonVariant';
import { EdisonPost } from './EdisonPost';
import { EDISON_FIELDS } from './schema';
import { EDISON_PRESETS } from './presets';

// Not a default first-run starter (`premium: true`) — free and fully usable
// like every variant, just not offered during setup.
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
