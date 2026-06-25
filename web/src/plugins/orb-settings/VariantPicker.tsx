import { useRef, useState, useSyncExternalStore } from 'react';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Panel } from '@/components/ui/panel';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { pushStatusTransient } from '@/shared/statusBus';
import { variantRegistry } from '../orb/variants/registry';
import { setVariant } from '../orb/broadcast';
import { commitOrbNow } from './overrideWriter';
import { useOrbState } from '../orb/useOrbState';
import {
  importOrbisFile,
  isRuntimeOrb,
  removeRuntimeOrb,
} from '../orb/definitions/runtime';

/**
 * Variant picker. Hidden when only one variant is registered.
 * Also hosts the `.orbis` import flow — imported definitions appear in
 * the same list (they're normal registry entries) with a remove action
 * when the active variant is an imported one.
 */
export function VariantPicker() {
  const variants = useSyncExternalStore(
    variantRegistry.subscribe,
    variantRegistry.all,
    variantRegistry.all,
  );
  const { variantId } = useOrbState();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  // Switching variant resets palette + params to the new variant's
  // defaults; persist the whole orb block immediately so the pick
  // survives a relaunch (desktop localStorage doesn't — see
  // overrideWriter). Immediate, not debounced: the user may reload
  // right after picking, before a 400ms timer would fire.
  const onSelectVariant = (id: string) => {
    setVariant(id);
    commitOrbNow();
  };

  const onFile = async (file: File | undefined) => {
    if (!file || busy) return;
    setBusy(true);
    try {
      const res = await importOrbisFile(file);
      if (res.ok) {
        pushStatusTransient(`imported orb "${res.id}"`, 2400);
      } else {
        pushStatusTransient(`import failed: ${res.errors[0] ?? 'invalid file'}`, 4000);
        console.warn('[orb] .orbis import rejected:', res.errors);
      }
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const onRemove = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const ok = await removeRuntimeOrb(variantId);
      pushStatusTransient(ok ? 'orb removed' : 'remove failed', 2400);
    } finally {
      setBusy(false);
    }
  };

  if (variants.length < 2) return null;

  return (
    <Panel title="Style">
      <Field label="Orb variant" htmlFor="variant">
        <Select value={variantId} onValueChange={onSelectVariant}>
          <SelectTrigger id="variant" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {variants.map((v) => (
              <SelectItem key={v.id} value={v.id}>
                <span className="flex items-center gap-2">
                  {v.name}
                  {isRuntimeOrb(v.id) && (
                    <span className="text-micro uppercase tracking-wider text-fg-subtle">
                      Imported
                    </span>
                  )}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <div className="mt-2 flex items-center gap-2">
        <input
          ref={fileRef}
          type="file"
          accept=".orbis,application/json"
          className="hidden"
          onChange={(e) => onFile(e.target.files?.[0])}
        />
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => fileRef.current?.click()}
        >
          Import .orbis
        </Button>
        {isRuntimeOrb(variantId) && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={onRemove}>
            Remove this orb
          </Button>
        )}
      </div>
    </Panel>
  );
}
