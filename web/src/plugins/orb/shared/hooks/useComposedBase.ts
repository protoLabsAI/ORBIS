import { useMemo, useSyncExternalStore } from 'react';
import { useOrbState, useOrbOverrides } from '../../useOrbState';
import { composeBase } from '../../compose';
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
 */
export function useComposedBase<T>(
  voiceState: VoiceState,
): { base: T; effectiveState: VoiceState } {
  const { params } = useOrbState();
  const { stateOverrides, moodOverrides } = useOrbOverrides();
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

  const base = useMemo(
    () => composeBase(
      params as Record<string, number | string>,
      stateOverrides,
      moodOverrides,
      effectiveState,
      effectiveMood,
    ) as unknown as T,
    [
      params, stateOverrides, moodOverrides, effectiveState,
      effectiveMood.valence, effectiveMood.arousal, effectiveMood.guardedness,
    ],
  );

  return { base, effectiveState };
}
