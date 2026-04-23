/**
 * Edit `orb.state_overrides` / `orb.mood_overrides` maps in the store
 * and persist the result to the server (debounced).
 *
 * The server's merge_patch shallow-merges at the top-level block
 * level, so we always POST the full overrides maps — partial POSTs
 * would replace-not-merge the nested dict and lose sibling fields.
 */

import { api } from '@/lib/api';
import { orbStore } from '../orb/store';
import type {
  MoodOverrides,
  StateOverrides,
} from '../orb/compose';
import type { VoiceState } from '@/voice/state';
import type { MoodDim } from './AuthoringContext';

const SAVE_DEBOUNCE_MS = 400;

let _saveTimer: ReturnType<typeof setTimeout> | null = null;

/** Set one field in one state-override bucket. Zero-valued numbers
 * remove the entry so the override map stays minimal on disk. */
export function setStateDelta(
  state: VoiceState, key: string, value: number | string | null,
): void {
  const snap = orbStore.getSnapshot();
  const next: StateOverrides = deepClone(snap.stateOverrides);
  const bucket = { ...(next[state] ?? {}) };
  if (value === null || value === 0 || value === '') {
    delete bucket[key];
  } else {
    bucket[key] = value;
  }
  if (Object.keys(bucket).length === 0) {
    delete next[state];
  } else {
    next[state] = bucket;
  }
  orbStore.get().setOverrides(next, snap.moodOverrides);
  scheduleSave();
}

/** Set one field in one mood-override bucket. Same zero-strip as
 * state deltas. */
export function setMoodDelta(
  dim: MoodDim, key: string, value: number | null,
): void {
  const snap = orbStore.getSnapshot();
  const next: MoodOverrides = deepClone(snap.moodOverrides);
  const bucket = { ...(next[dim] ?? {}) };
  if (value === null || value === 0) {
    delete bucket[key];
  } else {
    bucket[key] = value;
  }
  if (Object.keys(bucket).length === 0) {
    delete next[dim];
  } else {
    next[dim] = bucket;
  }
  orbStore.get().setOverrides(snap.stateOverrides, next);
  scheduleSave();
}

/** Clear every delta under a given state/dim bucket. */
export function resetBucket(ctx:
  | { kind: 'state'; state: VoiceState }
  | { kind: 'mood'; dim: MoodDim },
): void {
  const snap = orbStore.getSnapshot();
  if (ctx.kind === 'state') {
    const next = { ...snap.stateOverrides };
    delete next[ctx.state];
    orbStore.get().setOverrides(next, snap.moodOverrides);
  } else {
    const next = { ...snap.moodOverrides };
    delete next[ctx.dim];
    orbStore.get().setOverrides(snap.stateOverrides, next);
  }
  scheduleSave();
}

function scheduleSave(): void {
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(flushSave, SAVE_DEBOUNCE_MS);
}

/** Force an immediate save — useful when the panel unmounts mid-debounce. */
export async function flushSave(): Promise<void> {
  if (_saveTimer) {
    clearTimeout(_saveTimer);
    _saveTimer = null;
  }
  const snap = orbStore.getSnapshot();
  try {
    await api.putConfig({
      orb: {
        state_overrides: snap.stateOverrides as never,
        mood_overrides: snap.moodOverrides as never,
      },
    });
  } catch (e) {
    console.error('[orb-settings] save overrides failed', e);
  }
}

function deepClone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v));
}
