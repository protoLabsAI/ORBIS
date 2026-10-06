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

  test('older task cannot reclaim progress or outcome after a newer task finishes', () => {
    let lifecycle = initialDelegateLifecycle();
    for (const [task_id, state] of [['A', 'working'], ['B', 'working'], ['B', 'completed']]) {
      ({ lifecycle } = apply(lifecycle, 'delegate.status', { delegate_id: 'hub', task_id, state }));
    }
    expect(apply(lifecycle, 'delegate.status', {
      delegate_id: 'hub', task_id: 'A', state: 'working', text: 'late A',
    }).presentation?.patch).toEqual({});
    expect(apply(lifecycle, 'delegate.tool', {
      delegate_id: 'hub', task_id: 'A', status: 'started', name: 'late lookup',
    }).presentation?.patch).toEqual({});
    expect(apply(lifecycle, 'delegate.status', {
      delegate_id: 'hub', task_id: 'A', state: 'failed',
    }).presentation?.patch).toEqual({});
  });

  test('local tool lifecycle preserves an authoritative background task', () => {
    handleSse('__connected', '');
    handleSse('delegate.status', JSON.stringify({
      delegate_id: 'hub', task_id: 'background', state: 'working', text: 'researching',
    }));
    handleSse('tool-call', JSON.stringify({ event: 'start', name: 'check_inbox' }));
    handleSse('tool-call', JSON.stringify({ event: 'end', name: 'check_inbox', outcome: 'error' }));
    expect(voiceStore.getSnapshot()).toMatchObject({
      delegationTaskKey: 'hub\u001ftask:background', delegationProgress: 'hub: researching',
      delegationOutcome: null, activeToolCall: null,
    });
  });

  test('late tombstoned status cannot resurrect progress through its legacy mirror', () => {
    handleSse('__connected', '');
    handleSse('delegate.status', JSON.stringify({
      delegate_id: 'hub', task_id: 'done', state: 'completed',
    }));
    handleSse('delegate.status', JSON.stringify({
      delegate_id: 'hub', task_id: 'done', state: 'working', text: 'late update',
    }));
    handleSse('delegation-progress', JSON.stringify({ source: 'hub', text: 'late update' }));
    expect(voiceStore.getSnapshot()).toMatchObject({
      delegationTaskKey: null, delegationProgress: null, delegationOutcome: 'success',
    });
  });

  test('local tool completion preserves an authoritative delegate failure', () => {
    handleSse('__connected', '');
    handleSse('tool-call', JSON.stringify({ event: 'start', name: 'delegate_to' }));
    handleSse('delegate.status', JSON.stringify({
      delegate_id: 'hub', task_id: 'failed', state: 'failed',
    }));
    handleSse('tool-call', JSON.stringify({ event: 'end', name: 'delegate_to', outcome: 'success' }));
    expect(voiceStore.getSnapshot().delegationOutcome).toBe('error');
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

  test('interleaved structured callbacks cannot conceal an older task mirror', () => {
    handleSse('__connected', '');
    for (const [task_id, text] of [['A', 'A'], ['B', 'B'], ['A', 'late A'], ['B', 'fresh B']]) {
      handleSse('delegate.status', JSON.stringify({ delegate_id: 'hub', task_id, state: 'working', text }));
    }
    handleSse('delegation-progress', JSON.stringify({ source: 'hub', text: 'late A' }));
    expect(voiceStore.getSnapshot()).toMatchObject({
      delegationTaskKey: 'hub\u001ftask:B', delegationProgress: 'hub: fresh B',
    });
  });

  test('interleaved completed task callbacks cannot conceal a tombstoned mirror', () => {
    handleSse('__connected', '');
    handleSse('delegate.status', JSON.stringify({ delegate_id: 'hub', task_id: 'A', state: 'completed' }));
    handleSse('delegate.status', JSON.stringify({ delegate_id: 'hub', task_id: 'A', state: 'working', text: 'late A' }));
    handleSse('delegate.status', JSON.stringify({ delegate_id: 'hub', task_id: 'B', state: 'working', text: 'fresh B' }));
    handleSse('delegation-progress', JSON.stringify({ source: 'hub', text: 'late A' }));
    expect(voiceStore.getSnapshot()).toMatchObject({
      delegationTaskKey: 'hub\u001ftask:B', delegationProgress: 'hub: fresh B',
    });
  });

  test('repeated identical interleaved mirrors remain suppressed one per callback', () => {
    handleSse('__connected', '');
    handleSse('delegate.status', JSON.stringify({ delegate_id: 'hub', task_id: 'A', state: 'completed' }));
    for (let i = 0; i < 3; i++) {
      handleSse('delegate.status', JSON.stringify({ delegate_id: 'hub', task_id: 'A', state: 'working', text: 'late A' }));
      handleSse('delegate.status', JSON.stringify({ delegate_id: 'hub', task_id: 'B', state: 'working', text: `fresh B ${i}` }));
    }
    for (let i = 0; i < 3; i++) {
      handleSse('delegation-progress', JSON.stringify({ source: 'hub', text: 'late A' }));
    }
    expect(voiceStore.getSnapshot().delegationProgress).toBe('hub: fresh B 2');
  });
});
