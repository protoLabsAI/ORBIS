import { registerVariant } from '../registry';
import { CrystalVariant } from './CrystalVariant';
import { CRYSTAL_PRESETS } from './presets';
import { CRYSTAL_FIELDS } from './schema';

registerVariant({
  id: 'crystal',
  name: 'Crystal',
  description: 'Faceted icosahedron with PBR transmission + iridescence.',
  Component: CrystalVariant,
  palettes: CRYSTAL_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: CRYSTAL_FIELDS,
  defaultPalette: 'Prism',
});
