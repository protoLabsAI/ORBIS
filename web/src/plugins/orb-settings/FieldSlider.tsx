import { Field } from '@/components/ui/field';
import { Slider } from '@/components/ui/slider';
import { formatValue, type SliderField } from './fields';

export function FieldSlider({
  field,
  value,
  onChange,
}: {
  field: SliderField;
  value: number;
  onChange: (key: string, value: number) => void;
}) {
  const id = `orb-${field.key}`;
  return (
    <Field
      label={field.label}
      htmlFor={id}
      headerAside={
        <span className="font-mono text-xs text-zinc-500">{formatValue(value, field.step)}</span>
      }
    >
      <Slider
        id={id}
        min={field.min}
        max={field.max}
        step={field.step}
        value={[value]}
        onValueChange={(vals) => onChange(field.key, vals[0])}
      />
    </Field>
  );
}
