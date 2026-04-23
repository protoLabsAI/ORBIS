/**
 * Fetches ``/api/config`` once on mount and populates the orb store's
 * authoring override maps (`orb.state_overrides` + `orb.mood_overrides`).
 *
 * Deltas are authored per preset via the drawer editor (PR #6) or by
 * hand in config/orbis.yaml. Base params still live in localStorage as
 * their own mirror; overrides skip localStorage because they're small
 * and the server is the source of truth.
 *
 * Silent failure — if /api/config isn't reachable, overrides stay at
 * their default empty maps and the composition layer no-ops cleanly.
 */

import { api } from '@/lib/api';
import { orbStore } from './store';
import type { MoodOverrides, StateOverrides } from './compose';

let _loaded = false;

export async function loadOrbOverrides(): Promise<void> {
  if (_loaded) return;
  _loaded = true;
  try {
    const { config } = await api.config();
    const state = (config?.orb?.state_overrides ?? {}) as StateOverrides;
    const mood = (config?.orb?.mood_overrides ?? {}) as MoodOverrides;
    orbStore.get().setOverrides(state, mood);
  } catch {
    // Keep the empty defaults; next-poll is not our job here.
    _loaded = false; // allow a retry if the caller re-invokes
  }
}
