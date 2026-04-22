/**
 * Field-schema types shared across orb variants + the settings panel.
 * Moved here (out of orb-settings/fields.ts) so variant plugins can
 * declare their own schemas without creating a circular dependency
 * with the settings panel.
 */

export type SectionId = 'color' | 'energy' | 'motion' | 'fractal' | 'perf';

export type ColorField = {
  kind: 'color';
  key: string;
  label: string;
  section: SectionId;
};

export type SliderField = {
  kind: 'slider';
  key: string;
  label: string;
  section: SectionId;
  min: number;
  max: number;
  step: number;
};

export type FieldSpec = ColorField | SliderField;

export const SECTIONS: Array<{ id: SectionId; label: string }> = [
  { id: 'color',   label: 'Color' },
  { id: 'energy',  label: 'Energy' },
  { id: 'motion',  label: 'Motion' },
  { id: 'fractal', label: 'Fractal' },
  { id: 'perf',    label: 'Performance' },
];

export function formatValue(v: number, step: number): string {
  if (Number.isInteger(step)) return String(Math.round(v));
  const decimals = step < 0.01 ? 3 : step < 0.1 ? 2 : step < 1 ? 1 : 0;
  return Number(v).toFixed(decimals);
}

export function randomHex(): string {
  return '#' + Math.floor(Math.random() * 0x1000000).toString(16).padStart(6, '0');
}

export function randomSliderValue(spec: SliderField): number {
  const steps = Math.max(1, Math.round((spec.max - spec.min) / spec.step));
  const n = Math.floor(Math.random() * (steps + 1));
  const v = spec.min + n * spec.step;
  return Number.isInteger(spec.step) ? Math.round(v) : Number(v.toFixed(4));
}

export function randomizeAll(fields: FieldSpec[]): Record<string, number | string> {
  const out: Record<string, number | string> = {};
  for (const f of fields) {
    out[f.key] = f.kind === 'color' ? randomHex() : randomSliderValue(f);
  }
  return out;
}

export function formatPresetValue(v: unknown, spec: FieldSpec): string {
  if (spec.kind === 'color') return `'${String(v)}'`;
  if (Number.isInteger(spec.step)) return String(Math.round(Number(v)));
  return String(Number(v));
}

export function formatPresetBlock(
  fields: FieldSpec[],
  params: Record<string, unknown>,
  name = 'NewPreset',
): string {
  const body = fields
    .map((s) => `    ${s.key}: ${formatPresetValue(params[s.key], s)},`)
    .join('\n');
  return `  ${name}: {\n${body}\n  },`;
}
