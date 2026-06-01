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
  // The runtime doesn't clamp composed uniforms today, but a delta
  // large enough to blow past the field's authored range usually
  // looks wrong (rotation overflows, negative density, etc.). Flag
  // it visually so the author knows their preset is pushing outside
  // what the variant was tuned for.
  const outOfRange = composed < field.min || composed > field.max;

  return (
    <Field
      label={field.label}
      htmlFor={id}
      headerAside={
        <div className="flex items-center gap-2 font-mono text-xs">
          <span className="text-fg-subtle">{formatValue(baseValue, field.step)}</span>
          <span className="text-fg-faint">→</span>
          <span
            className={
              outOfRange ? 'text-danger' : hasDelta ? 'text-brand' : 'text-fg-subtle'
            }
            title={outOfRange ? `outside authored range [${field.min}, ${field.max}]` : undefined}
          >
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
                ? 'text-fg-muted hover:text-fg-body hover:bg-edge'
                : 'text-edge cursor-not-allowed')
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
