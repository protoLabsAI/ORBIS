import { registerVariant } from '../registry';
import { GeodeVariant } from './GeodeVariant';
import { GeodePost } from './GeodePost';
import { GEODE_FIELDS } from './schema';
import { GEODE_PRESETS } from './presets';

// Premium (paid): not in the free starter pool; `premium` gates it behind the
// customization paywall in the editor/picker.
registerVariant({
  id: 'geode',
  name: 'Geode',
  description: 'A glowing octahedron of plasma, caged in neon wireframe.',
  Component: GeodeVariant,
  PostEffects: GeodePost,
  palettes: GEODE_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: GEODE_FIELDS,
  defaultPalette: 'Geode',
  premium: true,
});
