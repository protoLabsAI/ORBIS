import { useCallback, useMemo, useSyncExternalStore } from 'react';
import { voiceStore, type VoiceSnapshot } from './state';
import { effectiveVoiceLifecycle } from './lifecycle';

/**
 * Full derived snapshot. Re-renders on every store epoch tick.
 */
export function useVoiceState(): VoiceSnapshot {
  return useSyncExternalStore(voiceStore.subscribe, voiceStore.getSnapshot, voiceStore.getSnapshot);
}

/**
 * Select a slice of the snapshot. Re-renders only when the selected
 * value changes by referential equality. Use for perf-sensitive
 * consumers (the orb reads only `state`).
 */
export function useVoiceStateSelector<T>(selector: (s: VoiceSnapshot) => T): T {
  const get = useCallback(() => selector(voiceStore.getSnapshot()), [selector]);
  return useSyncExternalStore(voiceStore.subscribe, get, get);
}


export function useEffectiveVoiceLifecycle() {
  const lifecycle = useVoiceStateSelector((s) => s.voiceLifecycle);
  const audio = useVoiceStateSelector((s) => s.nativeAudio);
  return useMemo(() => effectiveVoiceLifecycle(lifecycle, audio), [lifecycle, audio]);
}
