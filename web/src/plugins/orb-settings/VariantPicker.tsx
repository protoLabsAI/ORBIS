import { useSyncExternalStore } from 'react';
import { Field } from '@/components/ui/field';
import { Panel } from '@/components/ui/panel';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { variantRegistry } from '../orb/variants/registry';
import { setVariant } from '../orb/broadcast';
import { useOrbState } from '../orb/useOrbState';

/**
 * Variant picker. Hidden when only one variant is registered.
 */
export function VariantPicker() {
  const variants = useSyncExternalStore(
    variantRegistry.subscribe,
    variantRegistry.all,
    variantRegistry.all,
  );
  const { variantId } = useOrbState();

  if (variants.length < 2) return null;

  return (
    <Panel title="Style">
      <Field label="Orb variant" htmlFor="variant">
        <Select value={variantId} onValueChange={setVariant}>
          <SelectTrigger id="variant" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {variants.map((v) => (
              <SelectItem key={v.id} value={v.id}>{v.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
    </Panel>
  );
}
