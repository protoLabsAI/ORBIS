import { useEffect, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { clamp01, easeInOutCubic } from '../math';
import { STATE_XFADE_MS } from '../constants';
import {
  lerpSnapshot,
  stateSnapshot,
  type OrbBasePreset,
  type StateSnapshot,
} from '../stateSnapshot';
import type { VoiceState } from '../../../../voice/state';

/**
 * 4-state crossfade machine. Interpolates the active StateSnapshot
 * over STATE_XFADE_MS whenever the voiceState changes. Also re-runs
 * the crossfade when the base preset changes (palette swap, slider
 * edit) so targets pick up the new base.
 *
 * Returns a ref containing the current interpolated snapshot.
 * Callers read `snapRef.current` inside their own useFrame.
 */
export function useStateCrossfade(voiceState: VoiceState, base: OrbBasePreset) {
  const baseRef = useRef(base);
  baseRef.current = base;

  const snapRef = useRef<StateSnapshot>(stateSnapshot('idle', base));

  const cur = useRef({
    active: 'idle' as VoiceState,
    from: snapRef.current,
    to: snapRef.current,
    xfadeStart: 0,
    xfadeActive: false,
    enteredMs: performance.now(),
  });

  const beginCrossfade = (next: VoiceState) => {
    const c = cur.current;
    c.from = {
      ...snapRef.current,
      primary: snapRef.current.primary.clone(),
      secondary: snapRef.current.secondary.clone(),
    };
    c.to = stateSnapshot(next, baseRef.current);
    c.xfadeStart = performance.now();
    c.xfadeActive = true;
  };

  // Kick a crossfade whenever voiceState changes.
  useEffect(() => {
    const c = cur.current;
    if (voiceState !== c.active) {
      c.active = voiceState;
      c.enteredMs = performance.now();
      beginCrossfade(voiceState);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceState]);

  // Re-run crossfade when state-driven base fields change.
  useEffect(() => {
    beginCrossfade(cur.current.active);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    base.primaryEnergy, base.secondaryEnergy, base.density, base.atmosphereGlow,
    base.speed, base.chromaticAberration, base.asymmetry, base.orbRotation,
  ]);

  useFrame(() => {
    const c = cur.current;
    if (!c.xfadeActive) return;
    const nowMs = performance.now();
    const t = clamp01((nowMs - c.xfadeStart) / STATE_XFADE_MS);
    const e = easeInOutCubic(t);
    snapRef.current = lerpSnapshot(c.from, c.to, e);
    if (t >= 1) {
      c.xfadeActive = false;
      snapRef.current = c.to;
    }
  });

  return { snapRef };
}
