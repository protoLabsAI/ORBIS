import { useEffect, useState } from 'react';
import { Field } from '@/components/ui/field';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { api, type Verbosity } from '@/lib/api';

const LEVELS: Verbosity[] = ['silent', 'brief', 'narrated', 'chatty'];

export function VerbositySelector() {
  const [level, setLevel] = useState<Verbosity | ''>('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.verbosity()
      .then((r) => setLevel(r.verbosity))
      .catch((e) => setError(String((e as Error).message ?? e)));
  }, []);

  const onChange = async (v: string) => {
    setLevel(v as Verbosity);
    try {
      const r = await api.setVerbosity(v as Verbosity);
      if (r.error) setError(r.error);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  return (
    <Field label="Verbosity" htmlFor="verbosity" error={error}>
      <Select value={level || undefined} onValueChange={onChange}>
        <SelectTrigger id="verbosity" className="w-full">
          <SelectValue placeholder="—" />
        </SelectTrigger>
        <SelectContent>
          {LEVELS.map((l) => (
            <SelectItem key={l} value={l}>{l}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}
