import { useEffect, useSyncExternalStore } from 'react';
import { useVoiceStateSelector } from '@/voice/hooks';
import { statusPillStore } from './store';

/**
 * Bottom-of-screen status hint.
 *
 * Pre-2026-04-28 this also showed connect/connecting/error states for
 * the WebRTC transport. The web/PWA path was dropped (DECISIONS.md
 * amendment of that date), so the native pipeline is always live and
 * the only cases we show are: a persistent hint, an externally-pushed
 * transient (from pushStatusTransient), or nothing.
 */
export function StatusPill() {
  const connected = useVoiceStateSelector((s) => s.connected);
  const externalTransient = useSyncExternalStore(
    statusPillStore.subscribe,
    statusPillStore.getSnapshot,
  );

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

  const text = externalTransient?.text
    ?? (connected ? 'just speak' : 'starting up…');

  return (
    <div
      className="pointer-events-none fixed left-1/2 -translate-x-1/2 z-10 text-zinc-400 text-xs font-mono tracking-wide text-center px-4"
      style={{ bottom: 'calc(2rem + env(safe-area-inset-bottom, 0px))' }}
    >
      {text}
    </div>
  );
}
