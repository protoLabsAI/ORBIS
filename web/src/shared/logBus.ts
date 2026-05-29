import { useSyncExternalStore } from 'react';

/**
 * In-memory ring buffer for native client-observed events: Tauri HTTP API
 * calls, SSE bridge lifecycle, and derived voice state. Producers call
 * `logBus.push({ source, level, message, data? })`; the Dev drawer tails the
 * same buffer through useSyncExternalStore.
 *
 * Bounded to MAX_EVENTS so a long voice session cannot grow memory forever.
 */
export type LogSource = 'api' | 'sse' | 'voice';
export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface LogEvent {
  ts: number;
  source: LogSource;
  level: LogLevel;
  message: string;
  data?: unknown;
}

const MAX_EVENTS = 500;

let buffer: LogEvent[] = [];
const listeners = new Set<() => void>();

export const logBus = {
  push(event: Omit<LogEvent, 'ts'>): void {
    const entry: LogEvent = { ts: Date.now(), ...event };
    // Replace the array instead of mutating in place so
    // useSyncExternalStore consumers see a fresh snapshot reference.
    buffer = buffer.length >= MAX_EVENTS
      ? [...buffer.slice(buffer.length - MAX_EVENTS + 1), entry]
      : [...buffer, entry];
    listeners.forEach((listener) => listener());
  },
  snapshot: (): readonly LogEvent[] => buffer,
  clear(): void {
    buffer = [];
    listeners.forEach((listener) => listener());
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

export function useLogBus(): readonly LogEvent[] {
  return useSyncExternalStore(logBus.subscribe, logBus.snapshot, logBus.snapshot);
}
