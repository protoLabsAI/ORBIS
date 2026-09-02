import { describe, expect, test } from 'bun:test';
import {
  initialDelegateLifecycle,
  reduceDelegateEvent,
} from '../src/voice/delegateEvents';
import { voiceStore } from '../src/voice/state';
import { handleSse } from '../src/voice/useVoiceBridge';

function apply(
  lifecycle: ReturnType<typeof initialDelegateLifecycle>,
  event: string,
  payload: Record<string, unknown>,
) {
  return reduceDelegateEvent(lifecycle, event, payload);
}

describe('structured delegate event lifecycle', () => {
  test('working status is correlated to its task', () => {
    const reduced = apply(initialDelegateLifecycle(), 'delegate.status', {
      delegate_id: 'hub', task_id: 'task-1', state: 'working', text: 'checking CI',
    });
    expect(reduced.presentation?.patch).toMatchObject({
      delegationTaskKey: 'hub\u001ftask:task-1',
      delegationProgress: 'hub: checking CI',
    });
    expect(reduced.presentation?.log.message).toBe('hub: working — checking CI');
  });

  test('terminal task A cannot clear active task B', () => {
    let lifecycle = initialDelegateLifecycle();
    ({ lifecycle } = apply(lifecycle, 'delegate.status', {
      delegate_id: 'hub', task_id: 'A', state: 'working', text: 'A work',
    }));
    ({ lifecycle } = apply(lifecycle, 'delegate.status', {
      delegate_id: 'hub', task_id: 'B', state: 'working', text: 'B work',
    }));
    const terminalA = apply(lifecycle, 'delegate.status', {
      delegate_id: 'hub', task_id: 'A', state: 'completed', text: 'A done',
    });

    expect(terminalA.lifecycle.activeTaskKey).toBe('hub\u001ftask:B');
    expect(terminalA.presentation?.patch).toEqual({});
  });

  test('late older task tool cannot overwrite newer active task', () => {
    let lifecycle = initialDelegateLifecycle();
    ({ lifecycle } = apply(lifecycle, 'delegate.status', {
      delegate_id: 'hub', task_id: 'A', state: 'working', text: 'A work',
    }));
    ({ lifecycle } = apply(lifecycle, 'delegate.status', {
      delegate_id: 'hub', task_id: 'B', state: 'working', text: 'B work',
    }));
    const lateA = apply(lifecycle, 'delegate.tool', {
      delegate_id: 'hub', task_id: 'A', name: 'lookup', status: 'started',
    });

    expect(lateA.lifecycle.activeTaskKey).toBe('hub\u001ftask:B');
    expect(lateA.presentation?.patch).toEqual({});
  });

  test('terminal tombstone rejects late status and tool frames', () => {
    let lifecycle = initialDelegateLifecycle();
    ({ lifecycle } = apply(lifecycle, 'delegate.status', {
      delegate_id: 'hub', task_id: 'A', state: 'completed', text: 'done',
    }));
    expect(apply(lifecycle, 'delegate.tool', {
      delegate_id: 'hub', task_id: 'A', name: 'web_search', status: 'started',
    }).presentation).toBeNull();
    expect(apply(lifecycle, 'delegate.status', {
      delegate_id: 'hub', task_id: 'A', state: 'working', text: 'late',
    }).presentation).toBeNull();
  });

  test('missing task id falls back to session then delegate identity', () => {
    expect(apply(initialDelegateLifecycle(), 'delegate.status', {
      delegate_id: 'hub', session_id: 'ctx', state: 'working',
    }).lifecycle.activeTaskKey).toBe('hub\u001fsession:ctx');
    expect(apply(initialDelegateLifecycle(), 'delegate.status', {
      delegate_id: 'hub', state: 'working',
    }).lifecycle.activeTaskKey).toBe('hub\u001fcurrent');
  });

  test('status/log strings and logged delta data stay bounded', () => {
    const status = apply(initialDelegateLifecycle(), 'delegate.status', {
      delegate_id: 'hub', task_id: 'A', state: 'working', text: `x${'🙂'.repeat(100_000)}`,
    }).presentation;
    expect(new TextEncoder().encode(status?.rawText ?? '').length).toBeLessThanOrEqual(1024);

    const delta = apply(initialDelegateLifecycle(), 'delegate.delta', {
      delegate_id: 'hub', task_id: 'A', deltas: Array(10_000).fill({ secret: 'x' }),
    }).presentation;
    expect(delta?.log.data).toEqual({
      delegate_id: 'hub', task_id: 'A', delta_count: 32,
    });
  });

  test('reconnect and session end clear unreconciled delegate state', () => {
    voiceStore.reset();
    handleSse('delegate.status', JSON.stringify({
      delegate_id: 'hub', task_id: 'A', state: 'working', text: 'stale',
    }));
    expect(voiceStore.getSnapshot().delegationProgress).toBe('hub: stale');

    handleSse('__connected', '');
    expect(voiceStore.getSnapshot()).toMatchObject({
      connected: true, delegationTaskKey: null, delegationProgress: null,
    });

    handleSse('delegate.status', JSON.stringify({
      delegate_id: 'hub', task_id: 'B', state: 'working', text: 'fresh',
    }));
    handleSse('session', JSON.stringify({ event: 'end' }));
    expect(voiceStore.getSnapshot()).toMatchObject({
      sessionId: null, delegationTaskKey: null, delegationProgress: null,
    });
  });

  test('task-blind compatibility mirror cannot repaint an older task', () => {
    voiceStore.reset();
    handleSse('__connected', '');
    handleSse('delegate.status', JSON.stringify({
      delegate_id: 'hub', task_id: 'A', state: 'working', text: 'A work',
    }));
    handleSse('delegation-progress', JSON.stringify({ source: 'hub', text: 'A work' }));
    handleSse('delegate.status', JSON.stringify({
      delegate_id: 'hub', task_id: 'B', state: 'working', text: 'B work',
    }));
    handleSse('delegate.status', JSON.stringify({
      delegate_id: 'hub', task_id: 'A', state: 'working', text: 'late A',
    }));
    handleSse('delegation-progress', JSON.stringify({ source: 'hub', text: 'late A' }));

    expect(voiceStore.getSnapshot()).toMatchObject({
      delegationTaskKey: 'hub\u001ftask:B',
      delegationProgress: 'hub: B work',
    });
  });
});
