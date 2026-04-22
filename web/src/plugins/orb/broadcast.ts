/**
 * Broadcast helpers. The settings panel writes through these; the
 * active variant component reads the resulting snapshot via useOrbState().
 *
 * Kept as a thin re-export of the underlying orbStore for API stability
 * during the migration. Consumers don't need to know whether there's one
 * orb or two (main + mobile preview); both subscribe to the same store.
 */

import { orbStore } from './store';
export { orbStore } from './store';
export type { OrbStateSnapshot } from './store';

export function applyParam(key: string, value: unknown): void {
  orbStore.get().setParam(key, value);
}

export function applyPreset(paletteName: string): void {
  orbStore.get().setPreset(paletteName);
}

export function setVariant(id: string): void {
  orbStore.get().setVariant(id);
}

export function loadCustomPreset(name: string): void {
  orbStore.get().loadCustomPreset(name);
}
