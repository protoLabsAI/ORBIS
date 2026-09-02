import type { PublicDelegateHealth } from '@/lib/api';

export interface BootStage {
  stage: string;
  detail: string;
}

export interface SsePayload {
  event: string;
  data: string;
}

export interface BootStatusRuntime {
  listenBoot: (handler: (raw: string) => void) => Promise<() => void>;
  listenSse: (handler: (payload: SsePayload) => void) => Promise<() => void>;
  bootSnapshot: () => Promise<string>;
  hubHealthSnapshot: () => Promise<SsePayload | null>;
}

interface BootSignalCallbacks {
  onBootStage: (stage: BootStage) => void;
  onHubUnavailable: () => void;
}

export interface BootSignals {
  applyBoot: (raw: string) => void;
  applySse: (payload: SsePayload) => void;
  cancel: () => void;
}

function parseBoot(raw: string): BootStage | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<BootStage>;
    return typeof value.stage === 'string'
      ? { stage: value.stage, detail: value.detail ?? '' }
      : null;
  } catch {
    return null;
  }
}

function parseHubHealth(payload: SsePayload): PublicDelegateHealth | null {
  if (payload.event !== 'delegate-health') return null;
  try {
    const value = JSON.parse(payload.data) as Partial<PublicDelegateHealth>;
    if (
      value.name !== 'hub'
      || typeof value.ok !== 'boolean'
      || typeof value.consecutive_failures !== 'number'
    ) return null;
    return value as PublicDelegateHealth;
  } catch {
    return null;
  }
}

/** Order-independent boot/health coordinator for one mounted effect. */
export function createBootSignals(callbacks: BootSignalCallbacks): BootSignals {
  let cancelled = false;
  let bootReady = false;
  let hubHealth: PublicDelegateHealth | null = null;
  let warned = false;

  const maybeWarn = () => {
    if (
      cancelled
      || !bootReady
      || warned
      || hubHealth?.ok !== false
      || hubHealth.consecutive_failures < 2
    ) return;
    warned = true;
    callbacks.onHubUnavailable();
  };

  return {
    applyBoot(raw) {
      const stage = parseBoot(raw);
      if (!stage || cancelled) return;
      callbacks.onBootStage(stage);
      if (stage.stage === 'ready') {
        bootReady = true;
        maybeWarn();
      }
    },
    applySse(payload) {
      const health = parseHubHealth(payload);
      if (!health || cancelled) return;
      hubHealth = health;
      maybeWarn();
    },
    cancel() {
      cancelled = true;
    },
  };
}

/**
 * Install each ephemeral listener before reading its authoritative Rust cache.
 * Returns synchronously so React cleanup can cancel delayed Tauri promises.
 */
export function startBootStatusRuntime(
  runtime: BootStatusRuntime,
  signals: BootSignals,
  onBootUnavailable: () => void,
): () => void {
  let cancelled = false;
  const unlisteners = new Set<() => void>();

  const connect = async <T>(
    listenForUpdates: (handler: (value: T) => void) => Promise<() => void>,
    readSnapshot: () => Promise<T | null>,
    apply: (value: T) => void,
    unavailable?: () => void,
  ) => {
    let listenerReady = false;
    try {
      const unlisten = await listenForUpdates(apply);
      listenerReady = true;
      if (cancelled) {
        unlisten();
        return;
      }
      unlisteners.add(unlisten);
    } catch {
      // Snapshot may still be available; try it before using the dev fallback.
    }
    if (cancelled) return;
    try {
      const snapshot = await readSnapshot();
      if (!cancelled && snapshot !== null) apply(snapshot);
    } catch {
      if (!cancelled && !listenerReady) unavailable?.();
    }
  };

  void connect<string>(
    runtime.listenBoot,
    runtime.bootSnapshot,
    signals.applyBoot,
    onBootUnavailable,
  );
  void connect<SsePayload>(
    runtime.listenSse,
    runtime.hubHealthSnapshot,
    signals.applySse,
  );

  return () => {
    cancelled = true;
    signals.cancel();
    for (const unlisten of unlisteners) unlisten();
    unlisteners.clear();
  };
}
