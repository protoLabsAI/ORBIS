/**
 * Polls ``/api/personality`` and feeds its mood field into moodStore.
 *
 * Starts on module load. Polls every 30s because personality drifts
 * on the order of days and mood on the order of minutes; sub-30s
 * polling burns requests for no perceptual gain.
 *
 * Silently swallows errors — if the server is unreachable, mood just
 * stays at its last known value. The orb never crashes because a
 * fetch failed.
 */

import { api } from '@/lib/api';
import { moodStore } from './moodStore';

const POLL_INTERVAL_MS = 30_000;
let _started = false;
let _timer: number | null = null;

async function _tick(): Promise<void> {
  try {
    const state = await api.personality();
    if (state.mood) {
      moodStore.set({
        valence: state.mood.valence,
        arousal: state.mood.arousal,
        guardedness: state.mood.guardedness,
        updatedAt: state.mood.updated_at,
      });
    }
  } catch {
    // fetch failed — keep the previous mood, try again next tick
  }
}

export function startMoodDriver(): void {
  if (_started) return;
  _started = true;
  // Kick immediately + then on the interval.
  _tick();
  _timer = window.setInterval(_tick, POLL_INTERVAL_MS);
}

export function stopMoodDriver(): void {
  if (_timer != null) {
    window.clearInterval(_timer);
    _timer = null;
  }
  _started = false;
}
