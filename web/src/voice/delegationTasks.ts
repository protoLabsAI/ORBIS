import { eventIdentity } from './delegateEvents';
import { humanizeToolCall } from './toolActivity';

/**
 * Per-task view of delegated work, keyed by the same taskKey the delegate
 * event reducer uses. Feeds the delegations widget (many tasks at once);
 * the single-task progress rail in voiceStore is unchanged.
 *
 * Pure reducer + a tiny external store. Terminal states are sticky, so
 * duplicate / out-of-order events never resurrect a finished task.
 */

export type DelegationStatus = 'working' | 'input' | 'done' | 'failed' | 'cancelled';

export interface DelegationTask {
  key: string;
  delegate: string;
  query: string;
  status: DelegationStatus;
  /** Last progress line (status text, tool phrase, or result preview). */
  progress: string;
  startedAt: number;
  endedAt: number | null;
}

export const MAX_DELEGATION_TASKS = 32;
const MAX_PROGRESS = 256;
const MAX_QUERY = 160;

export const isLive = (s: DelegationStatus): boolean => s === 'working' || s === 'input';

function clip(v: unknown, n: number): string {
  return typeof v === 'string' ? v.trim().slice(0, n) : '';
}

function fromEventState(state: string): DelegationStatus {
  switch (state) {
    case 'completed': return 'done';
    case 'failed': return 'failed';
    case 'canceled':
    case 'cancelled': return 'cancelled';
    case 'input-required':
    case 'input_required': return 'input';
    default: return 'working';
  }
}

function fromRowStatus(status: string): DelegationStatus {
  if (status === 'expired') return 'failed';
  return fromEventState(status);
}

/** Keep at most MAX tasks: evict oldest finished first, then oldest live. */
function bound(tasks: DelegationTask[]): DelegationTask[] {
  if (tasks.length <= MAX_DELEGATION_TASKS) return tasks;
  const out = [...tasks];
  const byAge = (a: DelegationTask, b: DelegationTask) => a.startedAt - b.startedAt;
  const evict = (pool: DelegationTask[]) => {
    for (const t of [...pool].sort(byAge)) {
      if (out.length <= MAX_DELEGATION_TASKS) return;
      out.splice(out.indexOf(t), 1);
    }
  };
  evict(out.filter((t) => !isLive(t.status)));
  evict(out.filter((t) => isLive(t.status)));
  return out;
}

function upsert(
  tasks: readonly DelegationTask[],
  key: string,
  make: () => DelegationTask,
  patch: (t: DelegationTask) => DelegationTask,
): DelegationTask[] {
  const i = tasks.findIndex((t) => t.key === key);
  if (i === -1) return bound([...tasks, make()]);
  const next = patch(tasks[i]);
  if (next === tasks[i]) return tasks as DelegationTask[];
  const copy = [...tasks];
  copy[i] = next;
  return copy;
}

/** Fold one delegate.* SSE event into the task list. */
export function reduceTasks(
  tasks: readonly DelegationTask[],
  event: string,
  payload: Record<string, unknown>,
  now: number,
): readonly DelegationTask[] {
  if (event !== 'delegate.status' && event !== 'delegate.tool') return tasks;
  const id = eventIdentity(payload);
  if (!id) return tasks;
  const blank = (): DelegationTask => ({
    key: id.taskKey, delegate: id.delegateId, query: '', status: 'working',
    progress: '', startedAt: now, endedAt: null,
  });

  if (event === 'delegate.tool') {
    if (payload.status !== 'started') return tasks;
    const phrase = humanizeToolCall(clip(payload.name, 128), undefined);
    return upsert(tasks, id.taskKey, () => ({ ...blank(), progress: phrase }),
      (t) => (isLive(t.status) ? { ...t, progress: phrase } : t));
  }

  const status = fromEventState(clip(payload.state, 64));
  const text = clip(payload.text, MAX_PROGRESS);
  return upsert(tasks, id.taskKey,
    () => ({
      ...blank(), status, progress: text,
      endedAt: isLive(status) ? null : now,
    }),
    (t) => {
      if (!isLive(t.status)) return t; // sticky terminal
      return {
        ...t,
        status,
        progress: text || t.progress,
        endedAt: isLive(status) ? null : now,
      };
    });
}

export interface DelegationRow {
  task_id: string;
  delegate: string;
  query?: string | null;
  status: string;
  result?: string | null;
  created_at: string;
  updated_at: string;
}

function ms(iso: string, fallback: number): number {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : fallback;
}

/** Merge a GET /api/delegations snapshot. Never regresses a local terminal. */
export function hydrateTasks(
  tasks: readonly DelegationTask[],
  rows: readonly DelegationRow[],
  now: number,
): readonly DelegationTask[] {
  let out: readonly DelegationTask[] = tasks;
  for (const row of rows) {
    if (!row?.task_id || !row.delegate) continue;
    const key = `${row.delegate}\u001ftask:${row.task_id}`;
    const status = fromRowStatus(row.status);
    const query = clip(row.query, MAX_QUERY);
    const result = clip(row.result, MAX_PROGRESS);
    out = upsert(out, key,
      () => ({
        key, delegate: row.delegate, query, status,
        progress: isLive(status) ? '' : (result || (row.status === 'expired' ? 'timed out' : '')),
        startedAt: ms(row.created_at, now),
        endedAt: isLive(status) ? null : ms(row.updated_at, now),
      }),
      (t) => {
        const withQuery = t.query || !query ? t : { ...t, query };
        if (!isLive(withQuery.status) || isLive(status)) return withQuery;
        return {
          ...withQuery, status,
          progress: result || withQuery.progress,
          endedAt: ms(row.updated_at, now),
        };
      });
  }
  return out;
}

export function createDelegationTaskStore() {
  let tasks: readonly DelegationTask[] = [];
  const listeners = new Set<() => void>();
  const set = (next: readonly DelegationTask[]) => {
    if (next === tasks) return;
    tasks = next;
    listeners.forEach((l) => l());
  };
  return {
    subscribe(l: () => void) {
      listeners.add(l);
      return () => { listeners.delete(l); };
    },
    getSnapshot: () => tasks,
    ingest(event: string, payload: Record<string, unknown>, now = Date.now()) {
      set(reduceTasks(tasks, event, payload, now));
    },
    hydrate(rows: readonly DelegationRow[], now = Date.now()) {
      set(hydrateTasks(tasks, rows, now));
    },
    reset() { set([]); },
  };
}

export const delegationTasks = createDelegationTaskStore();

/** Live (working / needs-input) task count — selector for the status pill. */
export const liveCount = (tasks: readonly DelegationTask[]): number =>
  tasks.filter((t) => isLive(t.status)).length;
