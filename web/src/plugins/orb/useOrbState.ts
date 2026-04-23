import { useSyncExternalStore } from 'react';
import { orbStore, type OrbStateSnapshot } from './store';
import { variantRegistry, type VariantSpec } from './variants/registry';
import type { MoodOverrides, StateOverrides } from './compose';

/** Full snapshot. Re-renders on every store epoch tick. */
export function useOrbState(): OrbStateSnapshot {
  return useSyncExternalStore(orbStore.subscribe, orbStore.getSnapshot, orbStore.getSnapshot);
}

/** The currently active variant's spec, or null if the registry is empty. */
export function useActiveVariant(): VariantSpec | null {
  const { variantId } = useOrbState();
  return variantRegistry.get(variantId) ?? null;
}

/** Stable refs to the authoring override maps. Variant renderers read
 * these inside useFrame and compose them against the current state +
 * mood on each frame — the composition is cheap, and keeping it per-
 * frame means mood scrubbing feels live. */
export function useOrbOverrides(): {
  stateOverrides: StateOverrides;
  moodOverrides: MoodOverrides;
} {
  const { stateOverrides, moodOverrides } = useOrbState();
  return { stateOverrides, moodOverrides };
}
