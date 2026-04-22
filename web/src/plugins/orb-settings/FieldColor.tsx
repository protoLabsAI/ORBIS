import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import type { ColorField } from './fields';

export function FieldColor({
  field,
  value,
  onChange,
}: {
  field: ColorField;
  value: string;
  onChange: (key: string, value: string) => void;
}) {
  const id = `orb-${field.key}`;
  const sync = (v: string) => {
    if (!/^#[0-9a-fA-F]{6}$/.test(v)) return;
    onChange(field.key, v.toLowerCase());
  };
  return (
    <Field label={field.label} htmlFor={id}>
      <div className="flex gap-2 items-center">
        <input
          id={id}
          type="color"
          value={value}
          onChange={(e) => sync(e.target.value)}
          className="h-9 w-12 cursor-pointer rounded border border-border bg-transparent"
        />
        <Input
          type="text"
          value={value.toLowerCase()}
          onChange={(e) => sync(e.target.value)}
          className="flex-1 h-9 font-mono text-xs"
        />
      </div>
    </Field>
  );
}
