import { registerVariant } from '../registry';
import { FractalVariant } from './FractalVariant';
import { FRACTAL_PRESETS } from './presets';
import { FRACTAL_FIELDS } from './schema';

registerVariant({
  id: 'fractal',
  name: 'Fractal',
  description: 'Ray-marched volumetric fractal with atmosphere shell.',
  Component: FractalVariant,
  palettes: FRACTAL_PRESETS as unknown as Record<string, Record<string, unknown>>,
  fields: FRACTAL_FIELDS,
  defaultPalette: 'Aurora',
});
