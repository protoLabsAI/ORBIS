import { Field } from '@/components/ui/field';
import { Slider } from '@/components/ui/slider';
import { formatValue, type SliderField } from './fields';

export function FieldSlider({
  field,
  value,
  onChange,
  onCommit,
}: {
  field: SliderField;
  value: number;
  onChange: (key: string, value: number) => void;
  /** Fires on pointer release (drag end) — persist the final value
   * immediately so a reload can't beat the live update's debounce. */
  onCommit?: (key: string) => void;
}) {
  const id = `orb-${field.key}`;
  return (
    <Field
      label={field.label}
      htmlFor={id}
      headerAside={
        <span className="font-mono text-xs text-fg-subtle">{formatValue(value, field.step)}</span>
      }
    >
      <Slider
        id={id}
        min={field.min}
        max={field.max}
        step={field.step}
        value={[value]}
        onValueChange={(vals) => onChange(field.key, vals[0])}
        onValueCommit={() => onCommit?.(field.key)}
      />
    </Field>
  );
}
