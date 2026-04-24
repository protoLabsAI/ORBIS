/**
 * Edit `orb.state_overrides` / `orb.mood_overrides` maps in the store
 * and persist the result to the server (debounced).
 *
 * The server's merge_patch shallow-merges at the top-level block
 * level, so we always POST the full overrides maps — partial POSTs
 * would replace-not-merge the nested dict and lose sibling fields.
 */

import { api, type OrbisConfig } from '@/lib/api';
import { orbStore } from '../orb/store';
import type {
  MoodOverrides,
  StateOverrides,
} from '../orb/compose';
import type { VoiceState } from '@/voice/state';
import type { MoodDim } from './AuthoringContext';

const SAVE_DEBOUNCE_MS = 400;

let _saveTimer: ReturnType<typeof setTimeout> | null = null;
// `_dirty` is set by the setters, cleared after a successful flush.
// Prevents flushSave() (called on panel unmount) from firing a POST
// when nothing has actually been authored in this session.
let _dirty = false;

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
  _dirty = true;
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
  _dirty = true;
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
  _dirty = true;
  scheduleSave();
}

function scheduleSave(): void {
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(flushSave, SAVE_DEBOUNCE_MS);
}

/** Force an immediate save — useful when the panel unmounts mid-
 * debounce. No-ops when nothing has been authored since the last
 * flush; skips the network round-trip entirely. */
export async function flushSave(): Promise<void> {
  if (_saveTimer) {
    clearTimeout(_saveTimer);
    _saveTimer = null;
  }
  if (!_dirty) return;
  const snap = orbStore.getSnapshot();
  const patch: { orb: NonNullable<OrbisConfig['orb']> } = {
    orb: {
      state_overrides: toWire(snap.stateOverrides),
      mood_overrides: toWire(snap.moodOverrides),
    },
  };
  try {
    await api.putConfig(patch);
    _dirty = false;
  } catch (e) {
    console.error('[orb-settings] save overrides failed', e);
    // Leave _dirty set so the next flush retries.
  }
}

/** Narrow the in-memory `ParamMap` (which allows bool|undefined) to
 * the JSON shape `OrbisConfig` declares — `number | string`. Drops
 * undefined values; keeps booleans as-is (server-side validator
 * accepts them for state deltas, drops them for mood deltas — see
 * agent/config_store.py). */
function toWire<K extends string>(
  overrides: Partial<Record<K, Record<string, number | string | boolean | undefined>>>,
): Partial<Record<K, Record<string, number | string>>> {
  const out: Partial<Record<K, Record<string, number | string>>> = {};
  for (const key of Object.keys(overrides) as K[]) {
    const bucket = overrides[key];
    if (!bucket) continue;
    const cleaned: Record<string, number | string> = {};
    for (const [k, v] of Object.entries(bucket)) {
      if (v === undefined) continue;
      if (typeof v === 'boolean') cleaned[k] = v ? 1 : 0;
      else cleaned[k] = v;
    }
    out[key] = cleaned;
  }
  return out;
}

function deepClone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v));
}
