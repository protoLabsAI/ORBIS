import { describe, expect, test } from 'bun:test';
import { presentDelegateEvent } from '../src/voice/delegateEvents';

describe('structured delegate event presentation', () => {
  test('working status drives the pill and a labeled log', () => {
    const view = presentDelegateEvent('delegate.status', {
      delegate_id: 'hub',
      task_id: 'task-1',
      state: 'working',
      text: 'checking CI',
    });
    expect(view?.progress).toBe('hub: checking CI');
    expect(view?.log).toEqual({ level: 'info', message: 'hub: working — checking CI' });
  });

  test('terminal status clears progress and reports outcome', () => {
    expect(presentDelegateEvent('delegate.status', {
      delegate_id: 'hub', state: 'completed', text: '',
    })).toMatchObject({ progress: null, outcome: 'success' });
    expect(presentDelegateEvent('delegate.status', {
      delegate_id: 'hub', state: 'failed', text: 'boom',
    })).toMatchObject({ progress: null, outcome: 'error' });
  });

  test('tool and delta events stay structured for logs', () => {
    expect(presentDelegateEvent('delegate.tool', {
      delegate_id: 'hub', name: 'web_search', status: 'started',
    })).toMatchObject({
      progress: 'hub: web_search…',
      log: { message: 'hub: web_search started' },
    });
    expect(presentDelegateEvent('delegate.delta', {
      delegate_id: 'hub', deltas: [{ domain: 'reminders' }],
    })?.log.message).toBe('hub: 1 world-state update');
  });

  test('malformed or unrelated events are ignored', () => {
    expect(presentDelegateEvent('delegate.status', { state: 'working' })).toBeNull();
    expect(presentDelegateEvent('bot-state', { delegate_id: 'hub' })).toBeNull();
  });
});
