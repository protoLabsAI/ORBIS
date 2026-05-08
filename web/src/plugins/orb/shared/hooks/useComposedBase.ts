import { useMemo, useSyncExternalStore } from 'react';
import { useOrbState, useOrbOverrides, useActiveVariant } from '../../useOrbState';
import { composeBase, type MoodOverrides } from '../../compose';
import { simulationStore } from '../../simulationStore';
import { moodStore } from '@/plugins/mood/moodStore';
import type { VoiceState } from '@/voice/state';

/**
 * Per-variant composition hook — the single entry point for
 * "turn palette + params + state/mood overrides + simulation pins
 * into an effective base preset for this frame."
 *
 * Extracted so Fractal / Nebula / Crystal / Particles don't each
 * have to thread the same five subscriptions + compose() call into
 * their top-of-component setup. Each variant consumes it as:
 *
 *   const { base, effectiveState } = useComposedBase<MyPreset>(voiceState);
 *   const { snapRef } = useStateCrossfade(effectiveState, base);
 *
 * The returned `effectiveState` respects simulation pins so variant
 * crossfade machines transition against the pinned state during
 * authoring previews. Live mood feeds composeBase as the default;
 * simulationStore.pinnedMood overrides it when the author is
 * previewing a specific mood dimension.
 *
 * Mood deltas are layered: the active variant's `moodDefaults` (baked
 * mappings shipped with the variant) merge with the user's authored
 * `moodOverrides` from the Customize panel, with user-authored entries
 * winning per-key. So out-of-the-box you get visible mood reactivity,
 * and authoring overrides anything you want to change.
 */
export function useComposedBase<T>(
  voiceState: VoiceState,
): { base: T; effectiveState: VoiceState } {
  const { params } = useOrbState();
  const { stateOverrides, moodOverrides } = useOrbOverrides();
  const variant = useActiveVariant();
  const liveMood = useSyncExternalStore(moodStore.subscribe, moodStore.get, moodStore.get);
  const sim = useSyncExternalStore(
    simulationStore.subscribe,
    simulationStore.getSnapshot,
    simulationStore.getSnapshot,
  );

  const effectiveState = sim.pinnedState ?? voiceState;
  const effectiveMood = sim.pinnedMood ?? {
    valence: liveMood.valence,
    arousal: liveMood.arousal,
    guardedness: liveMood.guardedness,
  };

  const mergedMoodOverrides = useMemo(
    () => mergeMoodOverrides(variant?.moodDefaults, moodOverrides),
    [variant, moodOverrides],
  );

  const base = useMemo(
    () => composeBase(
      params as Record<string, number | string>,
      stateOverrides,
      mergedMoodOverrides,
      effectiveState,
      effectiveMood,
    ) as unknown as T,
    [
      params, stateOverrides, mergedMoodOverrides, effectiveState,
      effectiveMood.valence, effectiveMood.arousal, effectiveMood.guardedness,
    ],
  );

  return { base, effectiveState };
}

/** Merge variant defaults with user overrides — user wins per-key, so
 * authoring `moodOverrides.valence.atmosphereGlow = 0.1` replaces just
 * that one delta without losing the variant's other valence deltas. */
function mergeMoodOverrides(
  defaults: MoodOverrides | undefined,
  user: MoodOverrides,
): MoodOverrides {
  if (!defaults) return user;
  const out: MoodOverrides = {};
  for (const dim of ['valence', 'arousal', 'guardedness'] as const) {
    const d = defaults[dim];
    const u = user[dim];
    if (!d && !u) continue;
    out[dim] = { ...(d ?? {}), ...(u ?? {}) };
  }
  return out;
}
