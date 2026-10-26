import { describe, expect, test } from 'bun:test';
import {
  SLOW_TOOL_MS,
  humanizeToolCall,
  toolActivityText,
} from '../src/voice/toolActivity';

describe('humanizeToolCall', () => {
  test('known tools', () => {
    expect(humanizeToolCall('render_widget')).toBe('opening a widget');
    expect(humanizeToolCall('check_inbox')).toBe('checking inbox');
    expect(humanizeToolCall('adjust_personality')).toBe('adjusting personality');
  });

  test('every shipped voice tool has a sensible phrase', () => {
    expect(humanizeToolCall('cancel_reminder')).toBe('cancelling a reminder');
    expect(humanizeToolCall('list_reminders')).toBe('checking reminders');
    expect(humanizeToolCall('schedule_reminder')).toBe('setting a reminder');
    expect(humanizeToolCall('switch_persona')).toBe('switching persona');
    expect(humanizeToolCall('orchestrate')).toBe('working on it');
  });

  test('delegate_to names its target, else generic', () => {
    expect(humanizeToolCall('delegate_to', { target: 'protoagent' })).toBe('asking protoagent');
    expect(humanizeToolCall('delegate_to', {})).toBe('delegating');
    expect(humanizeToolCall('delegate_to', null)).toBe('delegating');
  });

  test('search / fetch style tools', () => {
    for (const n of ['web_search', 'search_web', 'fetch_url', 'web_fetch', 'browse_page']) {
      expect(humanizeToolCall(n)).toBe('looking that up');
    }
  });

  test('unknown names never leak underscores or raw identifiers', () => {
    expect(humanizeToolCall('get_calendar_events')).toBe('getting calendar events');
    expect(humanizeToolCall('frobnicate_widget')).toBe('using frobnicate widget');
    expect(humanizeToolCall('doTheThing')).toBe('using do the thing');
    expect(humanizeToolCall('')).toBe('working');
    expect(humanizeToolCall('___')).toBe('working');
    for (const n of ['a_b_c', 'x-y', 'Some_Mixed-Name']) {
      expect(humanizeToolCall(n)).not.toContain('_');
    }
  });
});

describe('toolActivityText', () => {
  test('fast calls get an ellipsis, slow ones elapsed seconds', () => {
    expect(toolActivityText('check_inbox', {}, 0)).toBe('checking inbox…');
    expect(toolActivityText('check_inbox', {}, SLOW_TOOL_MS - 1)).toBe('checking inbox…');
    expect(toolActivityText('delegate_to', { target: 'protoagent' }, 12_400))
      .toBe('asking protoagent · 12s');
  });

  test('multiple live delegations collapse to a count', () => {
    expect(toolActivityText('delegate_to', { target: 'a' }, 0, 2)).toBe('2 tasks running…');
    expect(toolActivityText('delegate_to', { target: 'a' }, 5_000, 3)).toBe('3 tasks running · 5s');
    expect(toolActivityText('delegate_to', { target: 'a' }, 0, 1)).toBe('asking a…');
    // only delegate_to is affected
    expect(toolActivityText('check_inbox', {}, 0, 5)).toBe('checking inbox…');
  });
});
