import { useSyncExternalStore } from 'react';

const STORAGE_KEY = 'orbis.devMode';

/**
 * Developer tools — the Dev drawer tab + the dev-mode toggle in Settings —
 * are a BUILD-TIME capability, never a user-flippable one. Exposing them in a
 * shipped build has leaked dev-only surfaces before (the dev-gated premium-orb
 * surfaces, pre-2026-06-05; since removed outright).
 *
 * They're enabled only in a real dev server (`vite dev`) or when a build sets
 * `VITE_ORBIS_DEVTOOLS=1` — `nuke-and-rebuild.sh` sets it only with the
 * --devtools flag; default and CI release builds do NOT. In a shipped build
 * the flag is unset, so dev mode is permanently off and `set()` is a no-op —
 * a stale `orbis.devMode=1` in localStorage from an older build is ignored.
 */
export const DEVTOOLS_ENABLED: boolean =
  import.meta.env.DEV ||
  (import.meta.env as Record<string, string | undefined>).VITE_ORBIS_DEVTOOLS === '1';

const read = (): boolean => {
  if (!DEVTOOLS_ENABLED) return false;
  try {
    return localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
};

let current: boolean = read();
const listeners = new Set<() => void>();

export const devModeStore = {
  get: () => current,
  set: (value: boolean) => {
    if (!DEVTOOLS_ENABLED) return; // dev tools cannot be enabled in a shipped build
    if (value === current) return;
    current = value;
    try {
      if (value) localStorage.setItem(STORAGE_KEY, '1');
      else localStorage.removeItem(STORAGE_KEY);
    } catch {
      // localStorage can be unavailable in restricted webviews.
    }
    listeners.forEach((listener) => listener());
  },
  toggle: () => devModeStore.set(!current),
  subscribe: (listener: () => void) => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

export function useDevMode(): boolean {
  return useSyncExternalStore(devModeStore.subscribe, devModeStore.get, () => false);
}
