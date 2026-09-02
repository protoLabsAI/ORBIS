import { describe, expect, test } from 'bun:test';
import {
  createBootSignals,
  startBootStatusRuntime,
  type BootStatusRuntime,
  type SsePayload,
} from '../src/components/bootStatusRuntime';

const READY = JSON.stringify({ stage: 'ready', detail: 'Ready' });
const HUB_DOWN: SsePayload = {
  event: 'delegate-health',
  data: JSON.stringify({
    name: 'hub',
    type: 'a2a',
    ok: false,
    latency_ms: null,
    last_checked: 1,
    consecutive_failures: 2,
  }),
};

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function signalsWith(warnings: string[], stages: string[] = []) {
  return createBootSignals({
    onBootStage: (stage) => stages.push(stage.stage),
    onHubUnavailable: () => warnings.push('warn'),
  });
}

describe('boot signal ordering', () => {
  test('warns once whether ready or health arrives first', () => {
    for (const order of ['ready-first', 'health-first']) {
      const warnings: string[] = [];
      const signals = signalsWith(warnings);
      if (order === 'ready-first') {
        signals.applyBoot(READY);
        signals.applySse(HUB_DOWN);
      } else {
        signals.applySse(HUB_DOWN);
        signals.applyBoot(READY);
      }
      signals.applyBoot(READY);
      signals.applySse(HUB_DOWN);
      expect(warnings).toEqual(['warn']);
    }
  });

  test('cancelled sessions ignore late snapshots and events', () => {
    const warnings: string[] = [];
    const stages: string[] = [];
    const signals = signalsWith(warnings, stages);
    signals.cancel();
    signals.applyBoot(READY);
    signals.applySse(HUB_DOWN);
    expect(stages).toEqual([]);
    expect(warnings).toEqual([]);
  });
});

describe('native listener/snapshot handshake', () => {
  test('registers each listener before reading its cached snapshot', async () => {
    const sequence: string[] = [];
    const warnings: string[] = [];
    const runtime: BootStatusRuntime = {
      listenBoot: async () => {
        sequence.push('listen-boot');
        return () => {};
      },
      listenSse: async () => {
        sequence.push('listen-health');
        return () => {};
      },
      bootSnapshot: async () => {
        sequence.push('snapshot-boot');
        return READY;
      },
      hubHealthSnapshot: async () => {
        sequence.push('snapshot-health');
        return HUB_DOWN;
      },
    };

    const stop = startBootStatusRuntime(runtime, signalsWith(warnings), () => {});
    await flush();
    expect(sequence.indexOf('listen-boot')).toBeLessThan(sequence.indexOf('snapshot-boot'));
    expect(sequence.indexOf('listen-health')).toBeLessThan(
      sequence.indexOf('snapshot-health'),
    );
    expect(warnings).toEqual(['warn']);
    stop();
  });

  test('an event delivered while listener promises settle is not lost', async () => {
    const warnings: string[] = [];
    const stages: string[] = [];
    const runtime: BootStatusRuntime = {
      listenBoot: async (handler) => {
        handler(READY);
        return () => {};
      },
      listenSse: async (handler) => {
        handler(HUB_DOWN);
        return () => {};
      },
      bootSnapshot: async () => READY,
      hubHealthSnapshot: async () => HUB_DOWN,
    };

    const stop = startBootStatusRuntime(
      runtime, signalsWith(warnings, stages), () => {},
    );
    await flush();
    expect(stages.length).toBeGreaterThanOrEqual(1);
    expect(warnings).toEqual(['warn']);
    stop();
  });

  test('delayed listener registration gates snapshots without coupling channels', async () => {
    const bootListener = deferred<() => void>();
    const healthListener = deferred<() => void>();
    const snapshots: string[] = [];
    const warnings: string[] = [];
    const runtime: BootStatusRuntime = {
      listenBoot: () => bootListener.promise,
      listenSse: () => healthListener.promise,
      bootSnapshot: async () => {
        snapshots.push('boot');
        return READY;
      },
      hubHealthSnapshot: async () => {
        snapshots.push('health');
        return HUB_DOWN;
      },
    };

    const stop = startBootStatusRuntime(runtime, signalsWith(warnings), () => {});
    await flush();
    expect(snapshots).toEqual([]);

    healthListener.resolve(() => {});
    await flush();
    expect(snapshots).toEqual(['health']);
    expect(warnings).toEqual([]);

    bootListener.resolve(() => {});
    await flush();
    expect(snapshots).toEqual(['health', 'boot']);
    expect(warnings).toEqual(['warn']);
    stop();
  });

  test('cleanup closes delayed registrations without starting snapshots', async () => {
    const bootListener = deferred<() => void>();
    const healthListener = deferred<() => void>();
    const unlistened: string[] = [];
    const snapshots: string[] = [];
    const runtime: BootStatusRuntime = {
      listenBoot: () => bootListener.promise,
      listenSse: () => healthListener.promise,
      bootSnapshot: async () => {
        snapshots.push('boot');
        return READY;
      },
      hubHealthSnapshot: async () => {
        snapshots.push('health');
        return HUB_DOWN;
      },
    };

    const stop = startBootStatusRuntime(runtime, signalsWith([]), () => {});
    stop();
    bootListener.resolve(() => unlistened.push('boot'));
    healthListener.resolve(() => unlistened.push('health'));
    await flush();
    expect(unlistened.sort()).toEqual(['boot', 'health']);
    expect(snapshots).toEqual([]);
  });

  test('StrictMode-style effect replay still emits one warning', async () => {
    const warnings: string[] = [];
    const warningRef = { current: false };
    const runtime: BootStatusRuntime = {
      listenBoot: async () => () => {},
      listenSse: async () => () => {},
      bootSnapshot: async () => READY,
      hubHealthSnapshot: async () => HUB_DOWN,
    };
    const mount = () => startBootStatusRuntime(
      runtime,
      createBootSignals({
        onBootStage: () => {},
        onHubUnavailable: () => {
          if (warningRef.current) return;
          warningRef.current = true;
          warnings.push('warn');
        },
      }),
      () => {},
    );

    const firstStop = mount();
    await flush();
    firstStop();
    const secondStop = mount();
    await flush();
    expect(warnings).toEqual(['warn']);
    secondStop();
  });
});
