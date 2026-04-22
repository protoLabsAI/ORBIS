import { useSyncExternalStore } from 'react';
import { whoamiStore, type Whoami } from './whoami';

/**
 * Subscribe to the /api/whoami snapshot. Returns null until the boot
 * fetch completes.
 *
 * Helper selectors for the common UI gates:
 *   - `isAdmin(whoami)` — true for role=admin (including the synthetic
 *     single-user fallback, which runs as admin so dev isn't locked
 *     out).
 *   - `isSkillLocked(whoami)` — true when a non-admin user has exactly
 *     one allowed skill; UI should render a read-only chip.
 *   - `isVizLocked(whoami)` — true when a non-admin user has a
 *     pinned_viz OR their allowed_skills locks them to a single skill
 *     (that skill's own viz is then implicitly locked too).
 */
export function useWhoami(): Whoami | null {
  return useSyncExternalStore(whoamiStore.subscribe, whoamiStore.get, whoamiStore.get);
}

export function isAdmin(w: Whoami | null): boolean {
  return !!w && w.role === 'admin';
}

export function isSkillLocked(w: Whoami | null): boolean {
  return (
    !!w && w.role !== 'admin'
    && !!w.allowed_skills && w.allowed_skills.length === 1
  );
}

export function isVizLocked(w: Whoami | null): boolean {
  if (!w || w.role === 'admin') return false;
  if (w.pinned_viz) return true;
  return !!w.allowed_skills && w.allowed_skills.length === 1;
}
