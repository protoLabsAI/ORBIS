import { X } from 'lucide-react';
import { Field } from '@/components/ui/field';
import { Slider } from '@/components/ui/slider';
import { formatValue, type SliderField } from '../orb/shared/field-types';

/**
 * Edits a per-(state|mood) additive delta for a single numeric field.
 * Shows the delta value and the resulting composed preview
 * ("base → base+delta") so authors see the effect at a glance.
 *
 * Delta range = ±(field.max - field.min) * 0.5 with the same step as
 * the base slider. Enough to swing ±50% of the full field range,
 * which covers almost every useful authoring case; authors can go
 * further by editing config/orbis.yaml directly if they ever need to.
 */
export function FieldDeltaSlider({
  field,
  baseValue,
  delta,
  onChange,
  onReset,
}: {
  field: SliderField;
  baseValue: number;
  delta: number;
  onChange: (key: string, delta: number) => void;
  onReset: (key: string) => void;
}) {
  const id = `orb-delta-${field.key}`;
  const range = (field.max - field.min) * 0.5;
  const composed = baseValue + delta;
  const hasDelta = delta !== 0;

  return (
    <Field
      label={field.label}
      htmlFor={id}
      headerAside={
        <div className="flex items-center gap-2 font-mono text-xs">
          <span className="text-zinc-500">{formatValue(baseValue, field.step)}</span>
          <span className="text-zinc-600">→</span>
          <span className={hasDelta ? 'text-amber-300' : 'text-zinc-500'}>
            {formatValue(composed, field.step)}
          </span>
          <button
            type="button"
            aria-label={`Reset ${field.label}`}
            onClick={() => onReset(field.key)}
            disabled={!hasDelta}
            className={
              'h-4 w-4 grid place-items-center rounded-sm transition-colors ' +
              (hasDelta
                ? 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
                : 'text-zinc-700 cursor-not-allowed')
            }
          >
            <X className="h-3 w-3" strokeWidth={2} />
          </button>
        </div>
      }
    >
      <Slider
        id={id}
        min={-range}
        max={range}
        step={field.step}
        value={[delta]}
        onValueChange={(vals) => onChange(field.key, vals[0])}
      />
    </Field>
  );
}
