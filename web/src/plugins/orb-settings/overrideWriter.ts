/**
 * Persist the orb authoring state — variant / palette / base params /
 * `state_overrides` / `mood_overrides` — to the server (debounced).
 *
 * On the desktop build localStorage does NOT survive a relaunch: the
 * sidecar binds a fresh ephemeral port each launch, so the webview
 * lands on a new origin and configDriver re-hydrates the orb from
 * /api/config. The server config is therefore the only durable store,
 * and every panel edit funnels through here so it round-trips.
 *
 * All orb writes go through THIS single debounced + single-flight
 * writer on purpose. The server's merge_patch is a read-modify-write
 * that rewrites the whole config file (agent/config_store.py), so two
 * concurrent /api/config POSTs race — the slower request's stale read
 * clobbers the faster one's change. One writer ⇒ one in-flight POST ⇒
 * no race. We always POST the full block because merge_patch shallow-
 * merges at the orb-subkey level: a partial nested map replaces, not
 * merges, so omitting a field would not preserve it.
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
// `_dirty` is set by the setters, cleared when a save drains it.
// Prevents flushSave() (called on panel unmount) from firing a POST
// when nothing has actually been authored in this session.
let _dirty = false;
// Single-flight guard for the actual POST. A second flushSave() that
// lands while one is in flight waits on this promise rather than
// firing a concurrent request (which could race and let an older
// snapshot overwrite a newer one).
let _saveInFlight: Promise<void> | null = null;

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

/** Live-update a single base param during a slider/color drag. Debounced
 * — the stream of drag events coalesces into one POST. Pair with a
 * commit flush (slider release / input blur, via commitOrbNow) so a
 * reload right after the drag doesn't beat the 400ms timer. */
export function commitParam(key: string, value: unknown): void {
  orbStore.get().setParam(key, value);
  _dirty = true;
  scheduleSave();
}

/** Persist the current orb snapshot RIGHT NOW (no debounce). For the
 * discrete actions a user expects to stick immediately — variant /
 * palette switch, preset load, reset, randomize, and the commit edge of
 * a slider/color edit. A hard reload or relaunch tears the page down
 * without running React unmount cleanup, so debounce-then-flush-on-
 * unmount isn't enough on its own; an immediate flush has already
 * reached the sidecar by the time the user hits reload. The writer posts
 * the full snapshot — variant, palette, params and overrides together. */
export function commitOrbNow(): void {
  _dirty = true;
  void flushSave();
}

function scheduleSave(): void {
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => { void flushSave(); }, SAVE_DEBOUNCE_MS);
}

/** Force an immediate save — useful when the panel unmounts mid-
 * debounce. Single-flights: if a save is already in flight, waits on
 * it rather than firing a second request. Loops until `_dirty` is
 * drained so edits that land during an await get shipped in the
 * next round. No-ops when nothing is dirty. */
export async function flushSave(): Promise<void> {
  if (_saveTimer) {
    clearTimeout(_saveTimer);
    _saveTimer = null;
  }
  if (_saveInFlight) {
    await _saveInFlight;
    // `_saveInFlight` drains on its own loop; if it left us clean
    // (no new edits during its await), we're done.
    if (!_dirty) return;
  }
  if (!_dirty) return;
  _saveInFlight = runSaveLoop();
  try {
    await _saveInFlight;
  } finally {
    _saveInFlight = null;
  }
}

/** Drain `_dirty` by posting snapshots in sequence. Capturing the
 * snapshot BEFORE each await means edits that land during the
 * network round-trip don't get silently merged into this
 * transaction — they show up as still-dirty, and the loop picks
 * them up on the next pass. */
async function runSaveLoop(): Promise<void> {
  while (_dirty) {
    _dirty = false;
    const snap = orbStore.getSnapshot();
    const patch: { orb: NonNullable<OrbisConfig['orb']> } = {
      orb: {
        variant: snap.variantId,
        palette: snap.palette,
        params: snap.params,
        state_overrides: toWire(snap.stateOverrides),
        mood_overrides: toWire(snap.moodOverrides),
      },
    };
    try {
      await api.putConfig(patch);
    } catch (e) {
      // Re-arm so a future scheduleSave / flushSave retries. Break
      // out rather than spin — we don't want to hammer the server
      // on repeated failures, and the next user edit will trigger
      // a fresh attempt.
      _dirty = true;
      console.error('[orb-settings] save overrides failed', e);
      return;
    }
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

// Safety net for the debounced path. A hard reload / relaunch tears the
// page down without running the panel's flush-on-unmount effect, so a
// pending edit (mid-debounce) would be lost. Flush when the window is
// hidden or unloading. The Tauri api_request invoke is handed to the
// Rust core before teardown, so the POST still lands on the sidecar.
if (typeof window !== 'undefined') {
  const flushOnExit = (): void => { void flushSave(); };
  window.addEventListener('pagehide', flushOnExit);
  window.addEventListener('beforeunload', flushOnExit);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushOnExit();
  });
}
