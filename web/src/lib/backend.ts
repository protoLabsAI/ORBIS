/**
 * Backend base-URL resolution for the "hosted SPA, local sidecar"
 * deployment topology.
 *
 * Default behavior is a no-op — `apiUrl('/api/whoami')` returns
 * `/api/whoami`, hitting same-origin like ORBIS always has. Opt in
 * by setting either:
 *
 *   - ``VITE_ORBIS_BACKEND`` at build time (baked into the bundle), or
 *   - ``localStorage['orbis.backendUrl']`` at runtime (so the hosted
 *     SPA can prompt the user for their local sidecar URL after the
 *     pairing handshake — no rebuild needed)
 *
 * The pairing token (when configured) is layered on top by
 * `pairing.ts`; this module is only concerned with WHERE the requests
 * go, not what auth they carry.
 */

const STORAGE_BACKEND_URL = 'orbis.backendUrl';

/** Read the configured base URL. Empty string means "same-origin". */
export function backendBaseUrl(): string {
  // Runtime override always wins — hosted SPA needs to be reconfigurable
  // without a rebuild. localStorage may not exist in SSR / older
  // browsers; guard accordingly.
  try {
    const fromStorage = (typeof localStorage !== 'undefined'
      ? localStorage.getItem(STORAGE_BACKEND_URL)
      : null) ?? '';
    if (fromStorage) return stripTrailingSlash(fromStorage);
  } catch {
    // Storage access can throw in private-mode Safari etc.; fall back.
  }
  // Build-time default — typed via vite/client.d.ts. Casting because
  // we want this module to compile in both desktop and hosted builds
  // without forcing every consumer to expand env types.
  const fromEnv = (import.meta as unknown as { env?: Record<string, string> })
    .env?.VITE_ORBIS_BACKEND ?? '';
  return stripTrailingSlash(fromEnv);
}

/** Persist a runtime backend URL. Empty/null clears the override and
 * the SPA falls back to same-origin (or the build-time default). */
export function setBackendBaseUrl(url: string | null): void {
  try {
    if (url && url.trim()) {
      localStorage.setItem(STORAGE_BACKEND_URL, stripTrailingSlash(url.trim()));
    } else {
      localStorage.removeItem(STORAGE_BACKEND_URL);
    }
  } catch {
    // Same private-mode swallow as backendBaseUrl().
  }
}

/** Prepend the base URL to a path-relative endpoint. Pass-through for
 * absolute URLs and for empty bases (the same-origin default). */
export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const base = backendBaseUrl();
  if (!base) return path;
  return path.startsWith('/') ? `${base}${path}` : `${base}/${path}`;
}

function stripTrailingSlash(s: string): string {
  return s.endsWith('/') ? s.slice(0, -1) : s;
}
