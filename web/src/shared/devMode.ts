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
  set: (value: boolean) => {
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
