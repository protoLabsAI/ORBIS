/**
 * Transient status bus — a tiny pub-sub for surfacing short-lived messages in
 * the StatusPill from anywhere (a plugin action, a one-off error) without
 * polluting the derived voice-state stream.
 *
 * This is a core service, NOT owned by the status-pill plugin (which only
 * renders it): write with pushStatusTransient() from '@/sdk'; the StatusPill
 * subscribes to render it. Mirrors the useSyncExternalStore pattern in
 * voice/state.ts — intentionally tiny, no external dep.
 */

export interface PillTransient {
  text: string;
  expiresAt: number; // ms epoch; 0 = no auto-expire
}

type Listener = () => void;

class StatusBus {
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

export const statusBus = new StatusBus();

/**
 * Push a transient into the StatusPill. Defaults to 4s. Use for one-off errors
 * or actions that don't belong in the persistent voice-state snapshot;
 * happy-path state should derive from voice/state.ts.
 */
export function pushStatusTransient(text: string, ms = 4000): void {
  statusBus.push(text, ms);
}
