import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { RTVIEvent } from '@pipecat-ai/client-js';
import { useRTVIClientEvent, usePipecatClientTransportState } from '@pipecat-ai/client-react';
import { statusPillStore } from './store';

const IDLE_HINT = 'double-click the orb to start';
const CONNECTING_HINT = 'connecting…';
const CONNECTED_HINT = 'connected — speak';
const FADE_MS = 3000;

export function StatusPill() {
  const transport = usePipecatClientTransportState();
  const [transient, setTransient] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);
  const externalTransient = useSyncExternalStore(
    statusPillStore.subscribe,
    statusPillStore.getSnapshot,
  );

  const showTransient = (text: string, ms = FADE_MS) => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    setTransient(text);
    timerRef.current = window.setTimeout(() => setTransient(null), ms);
  };

  useRTVIClientEvent(RTVIEvent.BotReady, () => showTransient(CONNECTED_HINT));
  useRTVIClientEvent(RTVIEvent.Error, (m: unknown) => {
    const data = m as { data?: { error?: string } } | undefined;
    showTransient(`error: ${data?.data?.error ?? 'unknown'}`, 4000);
  });

  // Auto-expire the externally-pushed transient once its TTL hits.
  useEffect(() => {
    if (!externalTransient || externalTransient.expiresAt === 0) return;
    const remaining = externalTransient.expiresAt - Date.now();
    if (remaining <= 0) {
      statusPillStore.clear();
      return;
    }
    const id = window.setTimeout(() => statusPillStore.clear(), remaining);
    return () => window.clearTimeout(id);
  }, [externalTransient]);

  useEffect(() => {
    return () => {
      if (timerRef.current != null) window.clearTimeout(timerRef.current);
    };
  }, []);

  // External (connect-error pushed via store) wins over RTVI-driven
  // transients so an error surfacing during connect doesn't get
  // immediately overwritten by a stale BotReady toast.
  const overlay = externalTransient?.text ?? transient;

  // R11: "connecting…" while the transport is mid-handshake. Pulled
  // from transport state directly so it's a derived state, not a
  // transient — it stays on screen as long as the handshake is in
  // flight, and disappears the moment BotReady fires (which itself
  // pushes the 3s "connected — speak" toast).
  const connecting =
    transport === 'connecting' ||
    transport === 'authenticating' ||
    transport === 'connected';

  const disconnected = transport === 'disconnected' || transport === 'initialized' || transport === 'error';

  let text: string | null;
  if (overlay) {
    text = overlay;
  } else if (connecting) {
    text = CONNECTING_HINT;
  } else if (disconnected) {
    text = IDLE_HINT;
  } else {
    text = null;
  }

  if (!text) return null;

  return (
    <div
      className="pointer-events-none fixed left-1/2 -translate-x-1/2 z-10 text-zinc-400 text-xs font-mono tracking-wide text-center px-4"
      style={{ bottom: 'calc(2rem + env(safe-area-inset-bottom, 0px))' }}
    >
      {text}
    </div>
  );
}
