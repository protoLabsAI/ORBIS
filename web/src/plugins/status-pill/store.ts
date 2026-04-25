/**
 * Tiny transient store for the StatusPill so callers outside RTVI's
 * own event surface (e.g. raw client.connect() rejections) can
 * surface a short-lived message into the same UI as RTVI Error events.
 *
 * Mirrors the useSyncExternalStore pattern from voice/state.ts —
 * intentionally tiny, no external dep.
 */

export interface PillTransient {
  text: string;
  expiresAt: number; // ms epoch; 0 = no auto-expire
}

type Listener = () => void;

class StatusPillStore {
  private current: PillTransient | null = null;
  private listeners = new Set<Listener>();

  getSnapshot = (): PillTransient | null => this.current;

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };

  push(text: string, ms = 4000): void {
    this.current = { text, expiresAt: ms > 0 ? Date.now() + ms : 0 };
    this.listeners.forEach((l) => l());
  }

  clear(): void {
    if (this.current === null) return;
    this.current = null;
    this.listeners.forEach((l) => l());
  }
}

export const statusPillStore = new StatusPillStore();

/**
 * Push a transient into the StatusPill. Defaults to 4s — match the
 * Error-event duration so connect failures and RTVI errors look alike.
 *
 * Use this for connect/disconnect rejections and other surface that
 * doesn't ride RTVI events. Don't use it for happy-path state — those
 * should derive from transportState directly.
 */
export function pushStatusTransient(text: string, ms = 4000): void {
  statusPillStore.push(text, ms);
}
