/**
 * In-memory ring buffer for client-observed events — RTVI frames, REST
 * calls, WebRTC state transitions. Drives the Logs drawer tab. Bounded
 * to MAX entries so a long session can't blow the heap; oldest entries
 * drop first.
 *
 * Producers call `logBus.push({source, level, message, data?})`.
 * Consumers (LogsPanel) subscribe via useSyncExternalStore.
 */
import { useSyncExternalStore } from 'react';

export type LogSource = 'rtvi' | 'fetch' | 'webrtc' | 'voice';
export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface LogEvent {
  ts: number;
  source: LogSource;
  level: LogLevel;
  message: string;
  data?: unknown;
}

const MAX = 500;

let buffer: LogEvent[] = [];
const listeners = new Set<() => void>();

export const logBus = {
  push(e: Omit<LogEvent, 'ts'>): void {
    const entry: LogEvent = { ts: Date.now(), ...e };
    // Replace the array (rather than mutating in place) so React's
    // reference-equality check in useSyncExternalStore actually fires
    // the rerender. Without this the panel would silently miss updates.
    buffer = buffer.length >= MAX
      ? [...buffer.slice(buffer.length - MAX + 1), entry]
      : [...buffer, entry];
    listeners.forEach((l) => l());
  },
  snapshot: (): readonly LogEvent[] => buffer,
  clear(): void {
    buffer = [];
    listeners.forEach((l) => l());
  },
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  },
};

export function useLogBus(): readonly LogEvent[] {
  return useSyncExternalStore(logBus.subscribe, logBus.snapshot, logBus.snapshot);
}
