/**
 * Owner API key — single-value localStorage store.
 *
 * ORBIS is single-owner, multi-device. Auth exists to prevent a
 * tailnet neighbor from using your instance, not to separate users.
 * The owner's API key lives in config/users.yaml server-side; this
 * module holds the client-side copy so Tauri HTTP requests can attach
 * it as ``X-API-Key``.
 *
 * When the key is missing or wrong, the server returns 401 on
 * /api/* routes. The UI surfaces a settings panel to paste it in;
 * the wizard flow writes it here on first run.
 */

const STORAGE_KEY = 'orbis.apiKey';

type Listener = () => void;

let _key: string | null = typeof localStorage !== 'undefined'
  ? localStorage.getItem(STORAGE_KEY)
  : null;
const _listeners = new Set<Listener>();

export const apiKeyStore = {
  /** Current key, or null when none is set (dev / single-user mode). */
  get: (): string | null => _key,

  set: (next: string | null) => {
    _key = next && next.trim() ? next.trim() : null;
    try {
      if (_key === null) localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, _key);
    } catch {
      // localStorage can be unavailable in restricted webviews.
    }
    _listeners.forEach((l) => l());
  },

  clear: () => apiKeyStore.set(null),

  subscribe: (l: Listener): (() => void) => {
    _listeners.add(l);
    return () => {
      _listeners.delete(l);
    };
  },
};

/** Build a Headers object with the api key attached (if set). */
export function authHeaders(extra: HeadersInit = {}): Headers {
  const h = new Headers(extra);
  const key = apiKeyStore.get();
  if (key) h.set('X-API-Key', key);
  return h;
}
