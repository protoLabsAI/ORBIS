/**
 * Developer-mode flag — gates the Dev + Logs drawer tabs and any other
 * power-user knobs. Stored in localStorage so a refresh keeps the
 * setting; per-browser since dev mode is a personal preference, not
 * server config.
 */
import { useSyncExternalStore } from 'react';

const STORAGE_KEY = 'orbis.devMode';

const read = (): boolean => {
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
  set: (v: boolean) => {
    if (v === current) return;
    current = v;
    try {
      if (v) localStorage.setItem(STORAGE_KEY, '1');
      else localStorage.removeItem(STORAGE_KEY);
    } catch {}
    listeners.forEach((l) => l());
  },
  toggle: () => devModeStore.set(!current),
  subscribe: (l: () => void) => {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  },
};

export function useDevMode(): boolean {
  return useSyncExternalStore(devModeStore.subscribe, devModeStore.get, () => false);
}
