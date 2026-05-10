/**
 * Owner API key — single-value localStorage store.
 *
 * ORBIS is single-owner, multi-device. Auth exists to prevent a
 * tailnet neighbor from using your instance, not to separate users.
 * The owner's API key lives in config/users.yaml server-side; this
 * module holds the client-side copy so fetches can attach it as
 * ``X-API-Key`` and the WebRTC offer handshake includes it too.
 *
 * When the key is missing or wrong, the server returns 401 on
 * /api/* routes. The UI surfaces a settings panel to paste it in;
 * the wizard flow writes it here on first run.
 */

import { pairingStore } from './pairing';

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
    } catch {}
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

/** Build a Headers object with the owner API key attached (if set)
 * and the cross-origin pairing token (if set — empty in the historical
 * same-origin install, populated when the SPA is hosted separately
 * from the sidecar). The two are independent: API key is server-side
 * owner trust, pairing token is browser-tab anti-CSRF. */
export function authHeaders(extra: HeadersInit = {}): Headers {
  const h = new Headers(extra);
  const key = apiKeyStore.get();
  if (key) h.set('X-API-Key', key);
  // Imported lazily-via-static to avoid a circular dep — pairing.ts
  // doesn't import this module, so a top-of-file import is fine.
  const pair = pairingStore.get();
  if (pair) h.set('X-Orbis-Pair', pair);
  return h;
}
