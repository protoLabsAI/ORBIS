/**
 * Shared, persistent selected-microphone state.
 *
 * The legacy CPAL input path lets the user pick an input device from
 * the wizard and settings panel. The production macOS voice-processing
 * path follows the system input instead, so this store is used only
 * when the native shell reports a selectable input mode.
 *
 * Storage is a single `localStorage` slot. Empty string / missing
 * means "no preference, use system default."
 *
 * Subscriber API exists so the settings panel and the wizard step
 * can stay in sync within a single tab without lifting state — the
 * shared store fires its listeners on every set.
 */

const STORAGE_KEY = 'orbis.audio.inputDeviceId';

type Listener = (id: string) => void;
const listeners = new Set<Listener>();

function read(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}

function write(id: string): void {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // localStorage may be unavailable (e.g., Safari private mode).
    // Fall through; the in-memory listeners still fire so the
    // current session keeps the value, just doesn't persist.
  }
}

export function getPreferredAudioDeviceId(): string {
  return read();
}

export function setPreferredAudioDeviceId(id: string): void {
  write(id);
  for (const l of listeners) l(id);
}

export function subscribePreferredAudioDeviceId(l: Listener): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}
