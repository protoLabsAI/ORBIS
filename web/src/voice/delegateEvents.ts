import type { LogLevel } from '@/shared/logBus';

export interface DelegatePresentation {
  delegateId: string;
  rawText?: string;
  progress?: string | null;
  outcome?: 'success' | 'error' | null;
  log: { level: LogLevel; message: string };
}

/** Convert the structured native SSE contract into bounded UI presentation. */
export function presentDelegateEvent(
  event: string,
  payload: Record<string, unknown>,
): DelegatePresentation | null {
  const delegateId = typeof payload.delegate_id === 'string'
    ? payload.delegate_id.trim()
    : '';
  if (!delegateId) return null;

  if (event === 'delegate.status') {
    const state = typeof payload.state === 'string' ? payload.state : 'unknown';
    const rawText = typeof payload.text === 'string' ? payload.text.trim() : '';
    const terminal = ['completed', 'failed', 'canceled'].includes(state);
    const progress = terminal
      ? null
      : rawText
        ? `${delegateId}: ${rawText}`
        : undefined;
    return {
      delegateId,
      rawText,
      progress,
      outcome: state === 'completed'
        ? 'success'
        : state === 'failed'
          ? 'error'
          : terminal
            ? null
            : undefined,
      log: {
        level: state === 'failed' ? 'error' : state === 'canceled' ? 'warn' : 'info',
        message: `${delegateId}: ${state}${rawText ? ` — ${rawText}` : ''}`,
      },
    };
  }

  if (event === 'delegate.tool') {
    const name = typeof payload.name === 'string' && payload.name.trim()
      ? payload.name.trim()
      : 'tool';
    const status = typeof payload.status === 'string' ? payload.status : 'updated';
    return {
      delegateId,
      progress: status === 'started' ? `${delegateId}: ${name}…` : undefined,
      log: { level: 'info', message: `${delegateId}: ${name} ${status}` },
    };
  }

  if (event === 'delegate.delta') {
    const count = Array.isArray(payload.deltas) ? payload.deltas.length : 0;
    return {
      delegateId,
      log: {
        level: 'debug',
        message: `${delegateId}: ${count} world-state update${count === 1 ? '' : 's'}`,
      },
    };
  }

  return null;
}
