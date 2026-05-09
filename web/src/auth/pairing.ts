/**
 * Pairing-token store for the hosted-SPA + local-sidecar deployment
 * topology.
 *
 * Same-origin installs leave this empty and the API layer behaves
 * exactly as before — no header gets attached, the sidecar's pairing
 * middleware short-circuits when CORS isn't enforced, end-to-end
 * unchanged. The hosted SPA reads the token via the connect screen
 * (user pastes the value the sidecar prints on boot) and stores it
 * here; ``authHeaders()`` then attaches it to every request.
 */

const STORAGE_KEY = 'orbis.pairToken';

type Listener = () => void;

let _token: string | null = typeof localStorage !== 'undefined'
  ? localStorage.getItem(STORAGE_KEY)
  : null;
const _listeners = new Set<Listener>();

export const pairingStore = {
  /** Current pairing token, or null when none configured. */
  get: (): string | null => _token,

  set: (next: string | null) => {
    _token = next && next.trim() ? next.trim() : null;
    try {
      if (_token === null) localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, _token);
    } catch {
      // Same private-mode swallow as the api-key store.
    }
    _listeners.forEach((l) => l());
  },

  clear: () => pairingStore.set(null),

  subscribe: (l: Listener): (() => void) => {
    _listeners.add(l);
    return () => {
      _listeners.delete(l);
    };
  },
};
