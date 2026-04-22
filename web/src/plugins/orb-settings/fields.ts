/**
 * Kept as a re-export shim so existing imports don't churn during the
 * R3F migration. Types + helpers now live at `plugins/orb/shared/field-types`
 * and are consumed via `useActiveVariant()` for schema + palettes.
 */
export type {
  FieldSpec,
  ColorField,
  SliderField,
  SectionId,
} from '../orb/shared/field-types';
export {
  SECTIONS,
  formatValue,
  randomHex,
  randomSliderValue,
  randomizeAll,
  formatPresetValue,
  formatPresetBlock,
} from '../orb/shared/field-types';
