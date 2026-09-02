import type { LogLevel } from '@/shared/logBus';
import type { VoiceSnapshot } from './state';

const MAX_TERMINAL_TASKS = 128;
const MAX_TEXT_BYTES = 1024;
const MAX_FIELD_BYTES = 256;

function boundedText(value: unknown, maxBytes: number): string {
  if (typeof value !== 'string') return '';
  const encoder = new TextEncoder();
  let bounded = new TextDecoder().decode(encoder.encode(value.trim()).slice(0, maxBytes));
  while (encoder.encode(bounded).length > maxBytes) bounded = bounded.slice(0, -1);
  return bounded;
}

export interface DelegateLifecycleState {
  activeTaskKey: string | null;
  taskOrder: readonly string[];
  terminalTaskKeys: readonly string[];
}

export interface DelegatePresentation {
  delegateId: string;
  taskKey: string;
  rawText?: string;
  patch: Partial<VoiceSnapshot>;
  log: { level: LogLevel; message: string; data: Record<string, unknown> };
}

export interface DelegateReduction {
  lifecycle: DelegateLifecycleState;
  presentation: DelegatePresentation | null;
}

export function initialDelegateLifecycle(): DelegateLifecycleState {
  return { activeTaskKey: null, taskOrder: [], terminalTaskKeys: [] };
}

function eventIdentity(payload: Record<string, unknown>): {
  delegateId: string;
  taskKey: string;
  taskId: string;
  sessionId: string;
} | null {
  const delegateId = boundedText(payload.delegate_id, MAX_FIELD_BYTES);
  if (!delegateId) return null;
  const taskId = boundedText(payload.task_id, MAX_FIELD_BYTES);
  const sessionId = boundedText(payload.session_id, MAX_FIELD_BYTES);
  const scope = taskId ? `task:${taskId}` : sessionId ? `session:${sessionId}` : 'current';
  return { delegateId, taskKey: `${delegateId}\u001f${scope}`, taskId, sessionId };
}

function terminalKeysWith(
  state: DelegateLifecycleState,
  taskKey: string,
): readonly string[] {
  if (state.terminalTaskKeys.includes(taskKey)) return state.terminalTaskKeys;
  return [...state.terminalTaskKeys, taskKey].slice(-MAX_TERMINAL_TASKS);
}

function taskOrderWith(state: DelegateLifecycleState, taskKey: string): readonly string[] {
  if (state.taskOrder.includes(taskKey)) return state.taskOrder;
  return [...state.taskOrder, taskKey].slice(-MAX_TERMINAL_TASKS);
}

/** Reduce one authoritative delegate event monotonically by task identity. */
export function reduceDelegateEvent(
  lifecycle: DelegateLifecycleState,
  event: string,
  payload: Record<string, unknown>,
): DelegateReduction {
  const identity = eventIdentity(payload);
  if (!identity) return { lifecycle, presentation: null };
  const { delegateId, taskKey, taskId, sessionId } = identity;
  const correlation = {
    delegate_id: delegateId,
    ...(taskId ? { task_id: taskId } : {}),
    ...(sessionId ? { session_id: sessionId } : {}),
  };
  const isTombstoned = lifecycle.terminalTaskKeys.includes(taskKey);
  const knownTask = lifecycle.taskOrder.includes(taskKey);

  if (event === 'delegate.status') {
    const state = boundedText(payload.state, MAX_FIELD_BYTES) || 'unknown';
    const rawText = boundedText(payload.text, MAX_TEXT_BYTES);
    const terminal = ['completed', 'failed', 'canceled'].includes(state);
    if (isTombstoned) return { lifecycle, presentation: null };

    if (terminal) {
      const ownsVisibleState = lifecycle.activeTaskKey === null
        || lifecycle.activeTaskKey === taskKey;
      return {
        lifecycle: {
          activeTaskKey: ownsVisibleState ? null : lifecycle.activeTaskKey,
          taskOrder: taskOrderWith(lifecycle, taskKey),
          terminalTaskKeys: terminalKeysWith(lifecycle, taskKey),
        },
        presentation: {
          delegateId,
          taskKey,
          rawText,
          patch: ownsVisibleState ? {
            delegationTaskKey: null,
            delegationProgress: null,
            delegationOutcome: state === 'completed'
              ? 'success'
              : state === 'failed'
                ? 'error'
                : null,
          } : {},
          log: {
            level: state === 'failed' ? 'error' : state === 'canceled' ? 'warn' : 'info',
            message: `${delegateId}: ${state}${rawText ? ` — ${rawText}` : ''}`,
            data: { ...correlation, state },
          },
        },
      };
    }

    const progress = rawText ? `${delegateId}: ${rawText}` : `${delegateId}: ${state}`;
    const ownsVisibleState = lifecycle.activeTaskKey === null
      || lifecycle.activeTaskKey === taskKey
      || !knownTask;
    return {
      lifecycle: {
        ...lifecycle,
        activeTaskKey: ownsVisibleState ? taskKey : lifecycle.activeTaskKey,
        taskOrder: taskOrderWith(lifecycle, taskKey),
      },
      presentation: {
        delegateId,
        taskKey,
        rawText,
        patch: ownsVisibleState ? {
          delegationTaskKey: taskKey,
          delegationProgress: progress,
          delegationOutcome: null,
        } : {},
        log: {
          level: 'info',
          message: `${delegateId}: ${state}${rawText ? ` — ${rawText}` : ''}`,
          data: { ...correlation, state },
        },
      },
    };
  }

  if (isTombstoned) return { lifecycle, presentation: null };

  if (event === 'delegate.tool') {
    const name = boundedText(payload.name, MAX_FIELD_BYTES) || 'tool';
    const status = boundedText(payload.status, MAX_FIELD_BYTES) || 'updated';
    const started = status === 'started';
    const ownsVisibleState = started && (
      lifecycle.activeTaskKey === null
      || lifecycle.activeTaskKey === taskKey
      || !knownTask
    );
    return {
      lifecycle: {
        ...lifecycle,
        activeTaskKey: ownsVisibleState ? taskKey : lifecycle.activeTaskKey,
        taskOrder: taskOrderWith(lifecycle, taskKey),
      },
      presentation: {
        delegateId,
        taskKey,
        patch: ownsVisibleState ? {
          delegationTaskKey: taskKey,
          delegationProgress: `${delegateId}: ${name}…`,
          delegationOutcome: null,
        } : {},
        log: {
          level: 'info',
          message: `${delegateId}: ${name} ${status}`,
          data: { ...correlation, name, status },
        },
      },
    };
  }

  if (event === 'delegate.delta') {
    const count = Array.isArray(payload.deltas) ? Math.min(payload.deltas.length, 32) : 0;
    return {
      lifecycle,
      presentation: {
        delegateId,
        taskKey,
        patch: {},
        log: {
          level: 'debug',
          message: `${delegateId}: ${count} world-state update${count === 1 ? '' : 's'}`,
          data: { ...correlation, delta_count: count },
        },
      },
    };
  }

  return { lifecycle, presentation: null };
}
