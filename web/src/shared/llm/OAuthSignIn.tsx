/**
 * OAuth subscription sign-in — shared by the setup wizard and the Brain
 * settings panel so both surfaces drive the same account lifecycle
 * (status → sign in → poll/complete → disconnect) against the
 * `/api/llm/oauth/*` routes.
 *
 * Two flow shapes, decided by the backend's /start response:
 *   - `device` (ChatGPT/Codex): show a short user-code, open the verify
 *     URL, poll until approved.
 *   - `redirect` (Claude): open the authorize URL, user approves and
 *     Anthropic displays a `code#state` they paste back here.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Button } from '@/components/ui/button';
import { api } from '@/lib/api';

export interface OAuthProviderStatus {
  provider: string;
  signed_in: boolean;
  source: string;
  detail: string;
  hint: string;
  /** Credential health — null means genuinely unknown, not fine. */
  expires_at: number | null;
  refreshable: boolean | null;
  /** "managed" (renews itself on use) | "borrowed" (a CLI's login) | "static" (env token). */
  durability: string;
}

/** One muted line answering "will this sign-in fix itself?" — the green check
 * alone read identically for a self-renewing store, a borrowed CLI login that
 * dies when that sign-in goes stale, and a static env token. */
function durabilityNote(status: OAuthProviderStatus): string | null {
  switch (status.durability) {
    case 'managed':
      return 'Renews automatically.';
    case 'borrowed':
      return 'Borrowed from the CLI sign-in — stays alive only while that login is in use.';
    case 'static':
      return 'Static token — never refreshed; replace it when it expires.';
    default:
      return null;
  }
}

type FlowState =
  | { kind: 'idle' }
  | { kind: 'starting' }
  | {
      kind: 'device';
      flowId: string;
      userCode: string;
      verificationUri: string;
    }
  | { kind: 'redirect'; flowId: string; authorizeUrl: string; code: string; completing: boolean }
  | { kind: 'error'; message: string };

function openExternal(url: string) {
  // External links go through the open_url IPC (→ shell.open); a plain
  // anchor would navigate the app webview. Best-effort — the URL is also
  // displayed so the user can copy it.
  invoke('open_url', { url }).catch(() => {});
}

export function OAuthSignIn({
  provider,
  onStatusChange,
}: {
  provider: string;
  /** Fired whenever sign-in state is (re)learned — lets the host surface
   * gate its Continue/Save on a live credential. */
  onStatusChange?: (status: OAuthProviderStatus | null) => void;
}) {
  const [status, setStatus] = useState<OAuthProviderStatus | null>(null);
  const [flow, setFlow] = useState<FlowState>({ kind: 'idle' });
  const [busy, setBusy] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flowRef = useRef<string | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      const r = await api.llmOauth.status();
      const mine = r.providers.find((p) => p.provider === provider) ?? null;
      setStatus(mine);
      onStatusChange?.(mine);
    } catch {
      setStatus(null);
      onStatusChange?.(null);
    }
  }, [provider, onStatusChange]);

  useEffect(() => {
    void refreshStatus();
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
      // Abandon a half-finished flow so its device/PKCE state can't be
      // completed later (the backend also TTL-sweeps it).
      if (flowRef.current) void api.llmOauth.cancel(flowRef.current).catch(() => {});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const stopPolling = () => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  };

  const pollDevice = useCallback(
    (flowId: string, intervalS: number) => {
      pollTimer.current = setTimeout(async () => {
        try {
          const r = await api.llmOauth.poll(flowId);
          if (r.status === 'complete') {
            flowRef.current = null;
            setFlow({ kind: 'idle' });
            await refreshStatus();
            return;
          }
          if (r.status === 'error') {
            flowRef.current = null;
            setFlow({ kind: 'error', message: r.error ?? 'Sign-in failed.' });
            return;
          }
          pollDevice(flowId, intervalS);
        } catch (e) {
          flowRef.current = null;
          setFlow({ kind: 'error', message: String((e as Error).message ?? e) });
        }
      }, intervalS * 1000);
    },
    [refreshStatus],
  );

  const onSignIn = async () => {
    setFlow({ kind: 'starting' });
    try {
      const r = await api.llmOauth.start(provider);
      if (!r.ok || !r.flow_id) {
        setFlow({ kind: 'error', message: r.error ?? 'Could not start sign-in.' });
        return;
      }
      flowRef.current = r.flow_id;
      if (r.mode === 'device') {
        setFlow({
          kind: 'device',
          flowId: r.flow_id,
          userCode: r.user_code ?? '',
          verificationUri: r.verification_uri ?? '',
        });
        openExternal(r.verification_uri ?? '');
        pollDevice(r.flow_id, Math.max(3, r.interval ?? 5));
      } else {
        setFlow({
          kind: 'redirect',
          flowId: r.flow_id,
          authorizeUrl: r.authorize_url ?? '',
          code: '',
          completing: false,
        });
        openExternal(r.authorize_url ?? '');
      }
    } catch (e) {
      setFlow({ kind: 'error', message: String((e as Error).message ?? e) });
    }
  };

  const onCancel = async () => {
    stopPolling();
    if (flowRef.current) {
      void api.llmOauth.cancel(flowRef.current).catch(() => {});
      flowRef.current = null;
    }
    setFlow({ kind: 'idle' });
  };

  const onComplete = async () => {
    if (flow.kind !== 'redirect' || !flow.code.trim()) return;
    setFlow({ ...flow, completing: true });
    try {
      const r = await api.llmOauth.complete(flow.flowId, flow.code.trim());
      if (r.status === 'complete') {
        flowRef.current = null;
        setFlow({ kind: 'idle' });
        await refreshStatus();
      } else {
        setFlow({ ...flow, completing: false });
        setFlow({ kind: 'error', message: r.error ?? 'Sign-in failed.' });
      }
    } catch (e) {
      setFlow({ kind: 'error', message: String((e as Error).message ?? e) });
    }
  };

  const onDisconnect = async () => {
    setBusy(true);
    try {
      await api.llmOauth.disconnect(provider);
    } finally {
      setBusy(false);
      await refreshStatus();
    }
  };

  if (status?.signed_in) {
    return (
      <div className="rounded-md border border-success/30 bg-success/5 p-3 space-y-2">
        <div className="text-sm text-fg-body">
          <span className="text-success">✓ Signed in</span>
          {status.detail ? <span className="text-fg-muted"> — {status.detail}</span> : null}
        </div>
        {durabilityNote(status) ? (
          <div className="text-xs text-fg-muted">{durabilityNote(status)}</div>
        ) : null}
        <Button variant="secondary" size="sm" onClick={onDisconnect} disabled={busy}>
          {busy ? 'Disconnecting…' : 'Disconnect'}
        </Button>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-edge bg-raised/40 p-3 space-y-3">
      {flow.kind === 'idle' || flow.kind === 'starting' || flow.kind === 'error' ? (
        <>
          <div className="text-xs text-fg-muted">
            {status?.hint || 'Sign in with your account — no API key needed.'}
          </div>
          <Button size="sm" onClick={onSignIn} disabled={flow.kind === 'starting'}>
            {flow.kind === 'starting' ? 'Starting…' : 'Sign in'}
          </Button>
          {flow.kind === 'error' && (
            <div className="text-xs text-danger">✗ {flow.message}</div>
          )}
        </>
      ) : flow.kind === 'device' ? (
        <>
          <div className="text-xs text-fg-muted">
            Enter this code at{' '}
            <button
              type="button"
              className="underline text-fg-body"
              onClick={() => openExternal(flow.verificationUri)}
            >
              {flow.verificationUri.replace(/^https?:\/\//, '')}
            </button>
            {' '}— waiting for approval…
          </div>
          <div className="font-mono text-lg tracking-[0.3em] text-fg-body text-center py-1 select-all">
            {flow.userCode}
          </div>
          <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        </>
      ) : (
        <>
          <div className="text-xs text-fg-muted">
            Approve in the browser, then paste the code Anthropic shows you.{' '}
            <button
              type="button"
              className="underline text-fg-body"
              onClick={() => openExternal(flow.authorizeUrl)}
            >
              Reopen sign-in page
            </button>
          </div>
          <input
            value={flow.code}
            onChange={(e) => setFlow({ ...flow, code: e.target.value })}
            placeholder="paste code#state here"
            className="w-full h-9 rounded-md border border-edge bg-raised/60 px-3 text-sm text-fg-body placeholder-fg-muted font-mono"
            spellCheck={false}
            autoComplete="off"
          />
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={onComplete} disabled={!flow.code.trim() || flow.completing}>
              {flow.completing ? 'Verifying…' : 'Complete sign-in'}
            </Button>
            <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
          </div>
        </>
      )}
    </div>
  );
}
