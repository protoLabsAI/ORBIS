import { useSyncExternalStore } from 'react';
import { orbStore, type OrbStateSnapshot } from './store';
import { variantRegistry, type VariantSpec } from './variants/registry';

/** Full snapshot. Re-renders on every store epoch tick. */
export function useOrbState(): OrbStateSnapshot {
  return useSyncExternalStore(orbStore.subscribe, orbStore.getSnapshot, orbStore.getSnapshot);
}

/** The currently active variant's spec, or null if the registry is empty. */
export function useActiveVariant(): VariantSpec | null {
  const { variantId } = useOrbState();
  return variantRegistry.get(variantId) ?? null;
}
