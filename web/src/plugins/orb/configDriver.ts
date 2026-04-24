/**
 * Fetches ``/api/config`` once on mount and pushes the server's idea
 * of the orb (`orb.variant` / `orb.palette` / overrides) into the
 * client store. Server is the source of truth for these — localStorage
 * is just a per-origin cache, which on the desktop build is
 * effectively wiped every launch because the sidecar listens on a
 * fresh ephemeral port and the webview navigates to a new origin.
 *
 * Without this hydration the orb visualization renders whatever
 * localStorage's defaults are (fractal + Aurora), regardless of what
 * the user picked in the wizard or what got committed to
 * config/orbis.yaml. Overrides come along for the ride; same call
 * site, single GET.
 *
 * Silent failure — if /api/config isn't reachable, the existing
 * localStorage state stands.
 */

import { api } from '@/lib/api';
import { applyPreset, setVariant } from './broadcast';
import { orbStore } from './store';
import type { MoodOverrides, StateOverrides } from './compose';

let _loaded = false;

export async function loadOrbOverrides(): Promise<void> {
  if (_loaded) return;
  _loaded = true;
  try {
    const { config } = await api.config();
    if (config?.orb?.variant) setVariant(config.orb.variant);
    if (config?.orb?.palette) applyPreset(config.orb.palette);
    const state = (config?.orb?.state_overrides ?? {}) as StateOverrides;
    const mood = (config?.orb?.mood_overrides ?? {}) as MoodOverrides;
    orbStore.get().setOverrides(state, mood);
  } catch {
    // Keep the existing snapshot; next-poll is not our job here.
    _loaded = false; // allow a retry if the caller re-invokes
  }
}
