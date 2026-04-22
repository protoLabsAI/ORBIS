import { registerVariant } from '../registry';
import { NebulaVariant } from './NebulaVariant';
import { NEBULA_PRESETS } from './presets';
import { NEBULA_FIELDS } from './schema';

registerVariant({
  id: 'nebula',
  name: 'Nebula',
  description: 'Noise-based volumetric cloud — raymarched FBM with domain warp.',
  Component: NebulaVariant,
  palettes: NEBULA_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: NEBULA_FIELDS,
  defaultPalette: 'Andromeda',
});
