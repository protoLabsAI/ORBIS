import { describe, expect, test } from 'bun:test';
import {
  MAX_DELEGATION_TASKS,
  createDelegationTaskStore,
  hydrateTasks,
  liveCount,
  reduceTasks,
  type DelegationTask,
} from '../src/voice/delegationTasks';

const K = (d: string, id: string) => `${d}\u001ftask:${id}`;
const first = (tasks: readonly DelegationTask[]) => tasks[0];

describe('reduceTasks', () => {
  test('live -> progress -> done', () => {
    let t: readonly DelegationTask[] = [];
    t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'working', text: 'checking CI' }, 1000);
    expect(t).toHaveLength(1);
    expect(first(t)).toMatchObject({ key: K('hub', '1'), status: 'working', progress: 'checking CI', startedAt: 1000, endedAt: null });

    t = reduceTasks(t, 'delegate.tool', { delegate_id: 'hub', task_id: '1', name: 'web_search', status: 'started' }, 2000);
    expect(first(t)).toMatchObject({ status: 'working', progress: 'looking that up', startedAt: 1000 });

    t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'input-required', text: 'which repo?' }, 3000);
    expect(first(t).status).toBe('input');
    expect(liveCount(t)).toBe(1);

    t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'completed', text: 'all green' }, 4000);
    expect(first(t)).toMatchObject({ status: 'done', progress: 'all green', endedAt: 4000 });
    expect(liveCount(t)).toBe(0);
  });

  test('terminal is sticky: late/duplicate events do not resurrect or rewrite', () => {
    let t: readonly DelegationTask[] = [];
    t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'failed', text: 'boom' }, 1000);
    const done = t;
    t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'working', text: 'late' }, 2000);
    t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'completed' }, 3000);
    t = reduceTasks(t, 'delegate.tool', { delegate_id: 'hub', task_id: '1', name: 'x', status: 'started' }, 4000);
    expect(t).toBe(done);
    expect(first(t)).toMatchObject({ status: 'failed', progress: 'boom', endedAt: 1000 });
  });

  test('first event may be terminal (out of order); duplicates are idempotent', () => {
    let t: readonly DelegationTask[] = [];
    t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'canceled' }, 1000);
    expect(first(t)).toMatchObject({ status: 'cancelled', endedAt: 1000 });
    const again = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'canceled' }, 2000);
    expect(again).toBe(t);
  });

  test('tasks are independent; ignores unrelated/malformed events', () => {
    let t: readonly DelegationTask[] = [];
    t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'working' }, 1);
    t = reduceTasks(t, 'delegate.status', { delegate_id: 'ava', task_id: '2', state: 'working' }, 2);
    t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'completed' }, 3);
    expect(liveCount(t)).toBe(1);
    expect(reduceTasks(t, 'delegate.delta', { delegate_id: 'hub', task_id: '1' }, 4)).toBe(t);
    expect(reduceTasks(t, 'delegate.status', { task_id: '1', state: 'working' }, 4)).toBe(t);
    expect(reduceTasks(t, 'delegate.tool', { delegate_id: 'hub', task_id: '9', name: 'x', status: 'finished' }, 4)).toBe(t);
  });

  test('bounded: evicts oldest finished before live', () => {
    let t: readonly DelegationTask[] = [];
    t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: 'live', state: 'working' }, 0);
    for (let i = 1; i <= MAX_DELEGATION_TASKS + 10; i++) {
      t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: `d${i}`, state: 'completed' }, i);
    }
    expect(t.length).toBe(MAX_DELEGATION_TASKS);
    expect(t.some((x) => x.key === K('hub', 'live'))).toBe(true);
    expect(t.some((x) => x.key === K('hub', 'd1'))).toBe(false);
    expect(t.some((x) => x.key === K('hub', `d${MAX_DELEGATION_TASKS + 10}`))).toBe(true);
  });

  test('bounded even when everything is live', () => {
    let t: readonly DelegationTask[] = [];
    for (let i = 0; i < MAX_DELEGATION_TASKS + 5; i++) {
      t = reduceTasks(t, 'delegate.status', { delegate_id: 'hub', task_id: `l${i}`, state: 'working' }, i);
    }
    expect(t.length).toBe(MAX_DELEGATION_TASKS);
    expect(t.some((x) => x.key === K('hub', 'l0'))).toBe(false);
  });
});

describe('hydrateTasks', () => {
  const row = (over: Record<string, unknown> = {}) => ({
    task_id: '1', delegate: 'hub', query: 'fix the build', status: 'working',
    result: null, created_at: '2026-01-01T00:00:00+00:00', updated_at: '2026-01-01T00:00:30+00:00',
    ...over,
  }) as Parameters<typeof hydrateTasks>[1][number];

  test('adds unknown rows with real start/end times', () => {
    const t = hydrateTasks([], [row(), row({ task_id: '2', status: 'completed', result: 'merged' })], 0);
    expect(t).toHaveLength(2);
    expect(t[0]).toMatchObject({ status: 'working', query: 'fix the build', endedAt: null });
    expect(t[0].startedAt).toBe(Date.parse('2026-01-01T00:00:00+00:00'));
    expect(t[1]).toMatchObject({ status: 'done', progress: 'merged', endedAt: Date.parse('2026-01-01T00:00:30+00:00') });
  });

  test('fills query on an SSE-born row, keeps local progress', () => {
    let t: readonly DelegationTask[] = reduceTasks([], 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'working', text: 'step 2' }, 5);
    t = hydrateTasks(t, [row()], 10);
    expect(t[0]).toMatchObject({ query: 'fix the build', progress: 'step 2', startedAt: 5 });
  });

  test('adopts a terminal state missed while disconnected, never the reverse', () => {
    let t: readonly DelegationTask[] = reduceTasks([], 'delegate.status', { delegate_id: 'hub', task_id: '1', state: 'working' }, 5);
    t = hydrateTasks(t, [row({ status: 'completed', result: 'ok' })], 10);
    expect(t[0]).toMatchObject({ status: 'done', progress: 'ok' });
    const same = hydrateTasks(t, [row({ status: 'working' })], 20);
    expect(same[0].status).toBe('done');
  });

  test('expired maps to failed; junk rows ignored; bounded', () => {
    const t = hydrateTasks([], [row({ status: 'expired' }), { foo: 1 } as never], 0);
    expect(t).toHaveLength(1);
    expect(t[0]).toMatchObject({ status: 'failed', progress: 'timed out' });
    const many = Array.from({ length: 100 }, (_, i) => row({ task_id: `x${i}` }));
    expect(hydrateTasks([], many, 0).length).toBe(MAX_DELEGATION_TASKS);
  });
});

describe('store', () => {
  test('notifies only on change', () => {
    const s = createDelegationTaskStore();
    let n = 0;
    s.subscribe(() => { n++; });
    s.ingest('delegate.status', { delegate_id: 'hub', task_id: '1', state: 'working' });
    s.ingest('delegate.delta', { delegate_id: 'hub', task_id: '1' });
    s.ingest('delegate.status', { delegate_id: 'hub', task_id: '1', state: 'completed' });
    s.ingest('delegate.status', { delegate_id: 'hub', task_id: '1', state: 'working' });
    expect(n).toBe(2);
    expect(s.getSnapshot()[0].status).toBe('done');
  });
});
