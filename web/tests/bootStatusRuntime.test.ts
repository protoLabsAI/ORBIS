import { describe, expect, test } from 'bun:test';
import {
  createBootSignals,
  startBootStatusRuntime,
  type BootStatusRuntime,
  type SsePayload,
} from '../src/components/bootStatusRuntime';
import {
  parseVoiceLifecycle,
  voiceIsReady,
  voiceLifecycleText,
} from '../src/voice/lifecycle';

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
const VOICE_WARMING: SsePayload = {
  event: 'voice-lifecycle',
  data: JSON.stringify({ state: 'warming', detail: 'Loading voice models…' }),
};
const VOICE_FAILED: SsePayload = {
  event: 'voice-lifecycle',
  data: JSON.stringify({
    state: 'failed', detail: 'Voice startup failed', action: 'retry',
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
    onApplicationReady: () => {},
    onHubUnavailable: () => warnings.push('warn'),
    onVoiceLifecycle: () => {},
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

  test('app-ready releases the application while voice remains cold', () => {
    const stages: string[] = [];
    const lifecycle: string[] = [];
    const signals = createBootSignals({
      onBootStage: (stage) => stages.push(stage.stage),
      onApplicationReady: () => lifecycle.push('app-ready'),
      onHubUnavailable: () => {},
      onVoiceLifecycle: (voice) => lifecycle.push(voice.state),
    });

    signals.applySse(VOICE_WARMING);
    signals.applyBoot(JSON.stringify({ stage: 'app-ready', detail: 'Ready' }));

    expect(stages).toEqual(['app-ready']);
    expect(lifecycle).toEqual(['warming', 'app-ready']);
  });

  test('failed voice is surfaced without changing application readiness', () => {
    const stages: string[] = [];
    const lifecycle: string[] = [];
    const signals = createBootSignals({
      onBootStage: (stage) => stages.push(stage.stage),
      onApplicationReady: () => {},
      onHubUnavailable: () => {},
      onVoiceLifecycle: (voice) => lifecycle.push(`${voice.state}:${voice.detail}`),
    });

    signals.applyBoot(JSON.stringify({ stage: 'app-ready', detail: 'Ready' }));
    signals.applySse(VOICE_FAILED);

    expect(stages).toEqual(['app-ready']);
    expect(lifecycle).toEqual(['failed:Voice startup failed']);
  });

  test('explicit retry lifecycle replaces failure with real backend states', () => {
    const lifecycle: string[] = [];
    const signals = createBootSignals({
      onBootStage: () => {},
      onApplicationReady: () => {},
      onHubUnavailable: () => {},
      onVoiceLifecycle: (voice) => lifecycle.push(voice.state),
    });

    signals.applySse(VOICE_FAILED);
    signals.applySse(VOICE_WARMING);
    signals.applySse({
      event: 'voice-lifecycle',
      data: JSON.stringify({ state: 'starting', detail: 'Starting voice pipeline…' }),
    });
    signals.applySse({
      event: 'voice-lifecycle',
      data: JSON.stringify({ state: 'running', detail: 'Voice pipeline ready' }),
    });

    expect(lifecycle).toEqual(['failed', 'warming', 'starting', 'running']);
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
      voiceLifecycleSnapshot: async () => {
        sequence.push('snapshot-voice');
        return VOICE_WARMING;
      },
    };

    const stop = startBootStatusRuntime(runtime, signalsWith(warnings), () => {});
    await flush();
    expect(sequence.indexOf('listen-boot')).toBeLessThan(sequence.indexOf('snapshot-boot'));
    expect(sequence.indexOf('listen-health')).toBeLessThan(
      sequence.indexOf('snapshot-health'),
    );
    expect(sequence.indexOf('listen-health')).toBeLessThan(
      sequence.indexOf('snapshot-voice'),
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
      voiceLifecycleSnapshot: async () => VOICE_WARMING,
    };

    const stop = startBootStatusRuntime(
      runtime, signalsWith(warnings, stages), () => {},
    );
    await flush();
    expect(stages.length).toBeGreaterThanOrEqual(1);
    expect(warnings).toEqual(['warn']);
    stop();
  });

  test('cached lifecycle closes the event-before-WebView-listener gap', async () => {
    const lifecycle: string[] = [];
    const sequence: string[] = [];
    const runtime: BootStatusRuntime = {
      listenBoot: async () => () => {},
      listenSse: async () => {
        sequence.push('listen');
        return () => {};
      },
      bootSnapshot: async () => READY,
      hubHealthSnapshot: async () => null,
      voiceLifecycleSnapshot: async () => {
        sequence.push('snapshot');
        return VOICE_WARMING;
      },
    };
    const stop = startBootStatusRuntime(runtime, createBootSignals({
      onBootStage: () => {},
      onApplicationReady: () => {},
      onHubUnavailable: () => {},
      onVoiceLifecycle: (voice) => lifecycle.push(voice.state),
    }), () => {});

    await flush();
    expect(sequence).toEqual(['listen', 'snapshot']);
    expect(lifecycle).toEqual(['warming']);
    stop();
  });

  test('newer SSE lifecycle wins when it arrives during a stale snapshot read', async () => {
    const snapshot = deferred<SsePayload | null>();
    let emitSse!: (payload: SsePayload) => void;
    const lifecycle: string[] = [];
    const runtime: BootStatusRuntime = {
      listenBoot: async () => () => {},
      listenSse: async (handler) => {
        emitSse = handler;
        return () => {};
      },
      bootSnapshot: async () => READY,
      hubHealthSnapshot: async () => null,
      voiceLifecycleSnapshot: () => snapshot.promise,
    };
    const stop = startBootStatusRuntime(runtime, createBootSignals({
      onBootStage: () => {},
      onApplicationReady: () => {},
      onHubUnavailable: () => {},
      onVoiceLifecycle: (voice) => lifecycle.push(voice.state),
    }), () => {});

    await flush();
    emitSse({
      event: 'voice-lifecycle',
      data: JSON.stringify({ state: 'running', detail: 'Voice pipeline ready' }),
    });
    expect(lifecycle).toEqual(['running']);
    snapshot.resolve(VOICE_WARMING);
    await flush();

    expect(lifecycle).toEqual(['running']);
    stop();
  });

  test('a never-settling snapshot cannot withhold its live lifecycle domain', async () => {
    const never = new Promise<SsePayload | null>(() => {});
    let emitSse!: (payload: SsePayload) => void;
    const lifecycle: string[] = [];
    const runtime: BootStatusRuntime = {
      listenBoot: async () => () => {},
      listenSse: async (handler) => {
        emitSse = handler;
        return () => {};
      },
      bootSnapshot: async () => READY,
      hubHealthSnapshot: () => never,
      voiceLifecycleSnapshot: () => never,
    };
    const stop = startBootStatusRuntime(runtime, createBootSignals({
      onBootStage: () => {},
      onApplicationReady: () => {},
      onHubUnavailable: () => {},
      onVoiceLifecycle: (voice) => lifecycle.push(voice.state),
    }), () => {});

    await flush();
    emitSse({
      event: 'voice-lifecycle',
      data: JSON.stringify({ state: 'failed', detail: 'Voice stopped' }),
    });
    expect(lifecycle).toEqual(['failed']);
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
      voiceLifecycleSnapshot: async () => {
        snapshots.push('voice');
        return VOICE_WARMING;
      },
    };

    const stop = startBootStatusRuntime(runtime, signalsWith(warnings), () => {});
    await flush();
    expect(snapshots).toEqual([]);

    healthListener.resolve(() => {});
    await flush();
    expect(snapshots).toEqual(['health', 'voice']);
    expect(warnings).toEqual([]);

    bootListener.resolve(() => {});
    await flush();
    expect(snapshots).toEqual(['health', 'voice', 'boot']);
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
      voiceLifecycleSnapshot: async () => {
        snapshots.push('voice');
        return VOICE_WARMING;
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
      voiceLifecycleSnapshot: async () => VOICE_WARMING,
    };
    const mount = () => startBootStatusRuntime(
      runtime,
      createBootSignals({
        onBootStage: () => {},
        onApplicationReady: () => {},
        onHubUnavailable: () => {
          if (warningRef.current) return;
          warningRef.current = true;
          warnings.push('warn');
        },
        onVoiceLifecycle: () => {},
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

describe('voice interaction readiness', () => {
  test('only a running backend lifecycle enables voice', () => {
    expect(voiceIsReady(null)).toBeFalse();
    expect(voiceIsReady(parseVoiceLifecycle({ state: 'warming' }))).toBeFalse();
    expect(voiceIsReady(parseVoiceLifecycle({ state: 'starting' }))).toBeFalse();
    expect(voiceIsReady(parseVoiceLifecycle({ state: 'failed' }))).toBeFalse();
    expect(voiceIsReady(parseVoiceLifecycle({ state: 'running' }))).toBeTrue();
  });

  test('cold and failed states have honest status text', () => {
    expect(voiceLifecycleText(null)).toBe('voice starting…');
    expect(voiceLifecycleText(parseVoiceLifecycle({
      state: 'warming', detail: 'Loading Parakeet…',
    }))).toBe('Loading Parakeet…');
    expect(voiceLifecycleText(parseVoiceLifecycle({
      state: 'failed', detail: 'Voice pipeline stopped unexpectedly',
    }))).toBe('Voice pipeline stopped unexpectedly');
  });

  test('malformed lifecycle snapshots cannot enable interaction', () => {
    expect(parseVoiceLifecycle({ state: 'ready' })).toBeNull();
    expect(parseVoiceLifecycle({ state: 'running', detail: 42 })).toEqual({
      state: 'running', detail: '',
    });
    expect(parseVoiceLifecycle({
      state: 'failed', detail: 'Voice warmup failed', code: 'warmup_failed', action: 'retry',
    })).toEqual({
      state: 'failed', detail: 'Voice warmup failed', code: 'warmup_failed', action: 'retry',
    });
    expect(parseVoiceLifecycle({
      state: 'failed', action: 'invented',
    })?.action).toBeUndefined();
    expect(parseVoiceLifecycle(null)).toBeNull();
  });
});
