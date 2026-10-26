import { useEffect, useState } from 'react';

/**
 * Ambient, human wording for the status pill while a tool call is active.
 * Table-driven: add a row, not a branch. Unknown tools never leak a raw
 * snake_case identifier — they fall back to a de-snaked verb phrase.
 *
 * Pure text only. The per-tool stream stays in the logs panel.
 */

export const SLOW_TOOL_MS = 4_000;

type Args = unknown;

/** Exact tool name -> phrase (no trailing ellipsis). */
const EXACT: Record<string, string | ((args: Args) => string)> = {
  render_widget: 'opening a widget',
  check_inbox: 'checking inbox',
  adjust_personality: 'adjusting personality',
  set_orb_visual: 'restyling the orb',
  delegate_to: (args) => {
    const target = readString(args, 'target');
    return target ? `asking ${target}` : 'delegating';
  },
};

/** Name patterns, first match wins. */
const PATTERNS: Array<[RegExp, string]> = [
  [/(^|_)(web_?)?(search|fetch|browse|lookup|scrape)(_|$)|^(google|wikipedia)/, 'looking that up'],
  [/(^|_)(remember|recall|memory)(_|$)/, 'checking memory'],
  [/(^|_)(remind|reminder|timer)(_|$)/, 'setting a reminder'],
];

const VERBS: Record<string, string> = {
  get: 'getting', set: 'setting', list: 'listing', read: 'reading',
  create: 'creating', send: 'sending', search: 'searching', open: 'opening',
  run: 'running', check: 'checking', update: 'updating', delete: 'deleting',
  write: 'writing', fetch: 'fetching', add: 'adding', remove: 'removing',
  find: 'finding', save: 'saving', show: 'showing', play: 'playing',
  start: 'starting', stop: 'stopping',
};

function readString(args: Args, key: string): string | null {
  if (typeof args !== 'object' || args === null) return null;
  const v = (args as Record<string, unknown>)[key];
  return typeof v === 'string' && v.trim() ? v.trim() : null;
}

function words(name: string): string[] {
  return name
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[^A-Za-z0-9]+/g, ' ')
    .trim()
    .toLowerCase()
    .split(' ')
    .filter(Boolean);
}

/** Phrase for a tool call, e.g. `opening a widget`, `asking protoagent`. */
export function humanizeToolCall(name: string, args?: Args): string {
  const key = (name || '').trim();
  if (!key) return 'working';
  const exact = EXACT[key];
  if (exact) return typeof exact === 'function' ? exact(args) : exact;
  const lower = key.toLowerCase();
  for (const [re, phrase] of PATTERNS) if (re.test(lower)) return phrase;
  const w = words(key);
  if (w.length === 0) return 'working';
  const [first, ...rest] = w;
  const ing = VERBS[first];
  if (ing) return [ing, ...rest].join(' ');
  return `using ${w.join(' ')}`;
}

/**
 * Pill text: `phrase…` while fresh, `phrase · 12s` once slow. When more than
 * one delegation is live, a delegate_to call reads `N tasks running` instead
 * of naming a single target.
 */
export function toolActivityText(
  name: string,
  args: Args,
  elapsedMs: number,
  liveDelegations = 0,
): string {
  const phrase = name === 'delegate_to' && liveDelegations > 1
    ? `${liveDelegations} tasks running`
    : humanizeToolCall(name, args);
  if (elapsedMs < SLOW_TOOL_MS) return `${phrase}…`;
  return `${phrase} · ${Math.floor(elapsedMs / 1000)}s`;
}

/**
 * Text for the active tool call; ticks once a second after SLOW_TOOL_MS (same
 * timer-based escalation shape as useThinkingText). Resets when the call
 * object changes or ends.
 */
export function useToolActivityText(
  call: { name: string; args: unknown } | null,
  liveDelegations = 0,
): string | null {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    setElapsed(0);
    if (!call) return;
    const start = Date.now();
    let interval: number | undefined;
    const first = window.setTimeout(() => {
      setElapsed(Date.now() - start);
      interval = window.setInterval(() => setElapsed(Date.now() - start), 1_000);
    }, SLOW_TOOL_MS);
    return () => {
      window.clearTimeout(first);
      if (interval !== undefined) window.clearInterval(interval);
    };
  }, [call]);
  return call ? toolActivityText(call.name, call.args, elapsed,liveDelegations) : null;
}
