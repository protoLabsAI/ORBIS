/**
 * Connect screen for the hosted-SPA + local-sidecar deployment
 * topology.
 *
 * Mounts ahead of the main app when ``shouldShowConnect()`` is true —
 * either the SPA was built with a non-empty ``VITE_ORBIS_BACKEND`` (so
 * we know we're running in split-deployment mode) or the user has
 * already configured a backend URL once. Probes ``/healthz`` against
 * the configured backend and, when it succeeds, hands control to
 * <App>. Until then, shows install/start instructions and inputs for
 * the backend URL + pairing token.
 *
 * Same-origin installs never see this — ``shouldShowConnect()`` returns
 * false when no backend URL is configured anywhere. The historical
 * "open localhost:7866 and the SPA loads with the API" flow is
 * untouched.
 */

import { useEffect, useState } from 'react';
import { backendBaseUrl, setBackendBaseUrl } from '@/lib/backend';
import { pairingStore } from './pairing';

export function shouldShowConnect(): boolean {
  // Build-time hint: if the bundle was compiled with VITE_ORBIS_BACKEND
  // set, we're a hosted SPA and the connect flow is mandatory until
  // /healthz comes back ok. Same-origin builds leave this empty.
  const buildBackend = (
    import.meta as unknown as { env?: Record<string, string> }
  ).env?.VITE_ORBIS_BACKEND ?? '';
  if (buildBackend) return true;
  // Runtime opt-in (user previously configured): show until cleared.
  if (backendBaseUrl()) return true;
  return false;
}

type Status =
  | { kind: 'idle' }
  | { kind: 'probing' }
  | { kind: 'ok' }
  | { kind: 'unreachable'; detail: string }
  | { kind: 'unauthorized'; detail: string };

export function ConnectScreen({ onConnected }: { onConnected: () => void }) {
  const [url, setUrl] = useState<string>(() => backendBaseUrl() || 'http://127.0.0.1:7866');
  const [token, setToken] = useState<string>(() => pairingStore.get() ?? '');
  const [status, setStatus] = useState<Status>({ kind: 'idle' });

  // Probe automatically on mount when we already have a URL stored —
  // saves the user a click on the common case (return visit). Safe to
  // run unconditionally; an empty URL falls through to "unreachable".
  useEffect(() => {
    if (backendBaseUrl()) void probe(url, token, setStatus, onConnected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onConnect = async () => {
    setStatus({ kind: 'probing' });
    await probe(url, token, setStatus, onConnected);
  };

  return (
    <div className="fixed inset-0 bg-[#0a0a0a] text-zinc-100 flex items-center justify-center p-6">
      <div className="max-w-md w-full space-y-5">
        <h1 className="text-xl font-medium">Connect to your ORBIS sidecar</h1>
        <p className="text-sm text-zinc-400 leading-relaxed">
          ORBIS runs as a small process on your own machine. Start it with
          <code className="mx-1 px-1.5 py-0.5 bg-zinc-800 rounded text-xs">orbis</code>
          (or <code className="mx-1 px-1.5 py-0.5 bg-zinc-800 rounded text-xs">npx orbis</code>),
          then paste the URL it prints below along with the one-time pairing
          token.
        </p>

        <label className="block">
          <span className="text-xs uppercase tracking-wider text-zinc-500">
            Sidecar URL
          </span>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://127.0.0.1:7866"
            className="mt-1.5 w-full bg-zinc-900 border border-zinc-800 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-zinc-500"
          />
        </label>

        <label className="block">
          <span className="text-xs uppercase tracking-wider text-zinc-500">
            Pairing token
          </span>
          <input
            type="text"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="32-character hex string"
            className="mt-1.5 w-full bg-zinc-900 border border-zinc-800 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-zinc-500"
          />
        </label>

        <button
          type="button"
          onClick={onConnect}
          disabled={status.kind === 'probing'}
          className="w-full bg-zinc-100 text-zinc-900 rounded py-2 text-sm font-medium hover:bg-white disabled:opacity-60"
        >
          {status.kind === 'probing' ? 'Connecting…' : 'Connect'}
        </button>

        {status.kind === 'unreachable' && (
          <p className="text-sm text-red-400">
            Couldn't reach the sidecar at {url}. {status.detail}
          </p>
        )}
        {status.kind === 'unauthorized' && (
          <p className="text-sm text-amber-400">
            Sidecar reachable but rejected the pairing token. {status.detail}
          </p>
        )}
      </div>
    </div>
  );
}

async function probe(
  url: string,
  token: string,
  setStatus: (s: Status) => void,
  onConnected: () => void,
) {
  setStatus({ kind: 'probing' });
  // Build URLs from the candidate directly rather than routing through
  // apiUrl() — that would require persisting the candidate before
  // probing, and a failed probe would clobber the user's last-known-
  // good config in localStorage. We commit to storage only after both
  // /healthz and /api/whoami succeed.
  // Trim defensively — pasted-from-terminal values commonly carry
  // trailing whitespace, which makes fetch() throw on the URL and
  // breaks secrets.compare_digest on the token side. Storage already
  // trims internally, but normalizing here keeps the probe and the
  // saved values byte-identical.
  const trimmedUrl = url.trim();
  const trimmedToken = token.trim();
  const base = trimmedUrl.replace(/\/+$/, '');
  try {
    const r = await fetch(`${base}/healthz`);
    if (r.status === 200) {
      // Healthz is exempt from the pair check — that proves the sidecar
      // exists, but not that the token is valid. Make a second hit
      // against /api/whoami so we surface bad-token errors here rather
      // than at first feature use.
      const r2 = await fetch(`${base}/api/whoami`, {
        headers: trimmedToken ? { 'X-Orbis-Pair': trimmedToken } : {},
      });
      if (r2.status === 401) {
        setStatus({
          kind: 'unauthorized',
          detail: 'Check the token printed in the sidecar terminal.',
        });
        return;
      }
      if (!r2.ok) {
        setStatus({
          kind: 'unreachable',
          detail: `whoami returned HTTP ${r2.status}.`,
        });
        return;
      }
      // Success — commit to storage now. apiUrl() everywhere else in
      // the SPA will pick this up on next read; the pairing token is
      // attached by authHeaders() / voice/client.ts on every request.
      setBackendBaseUrl(trimmedUrl);
      pairingStore.set(trimmedToken);
      setStatus({ kind: 'ok' });
      onConnected();
      return;
    }
    setStatus({
      kind: 'unreachable',
      detail: `healthz returned HTTP ${r.status}.`,
    });
  } catch (e) {
    setStatus({
      kind: 'unreachable',
      detail: e instanceof Error ? e.message : 'Network error.',
    });
  }
}
