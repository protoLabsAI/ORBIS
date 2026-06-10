/**
 * Orb param composition — base + state delta + scaled mood delta →
 * effective uniforms.
 *
 * Per DECISIONS.md (2026-04-23 amendment), presets store deltas, not
 * absolutes. The voice-state machine picks which state bucket applies
 * at any moment; the mood store contributes a continuous per-dimension
 * blend. This module is the single place that knows the math —
 * variants call `composeBase()` and get back a ready-to-use param map.
 *
 * Value semantics:
 *   - Numeric delta + numeric base         → additive (base + delta)
 *   - Numeric mood delta × mood value       → additive, linearly scaled
 *   - String delta (e.g. "#ff00ff") on any  → replacement
 *   - Any delta key not present in base     → ignored (unknown uniform)
 */

import type { Mood, MoodOverrides, ParamMap, StateOverrides, VoiceState } from './types';

const MOOD_DIMS = ['valence', 'arousal', 'guardedness'] as const;

/**
 * Compose the effective base params for this instant. Pure function,
 * no I/O — caller decides when to call it (per-frame if they want
 * continuous mood reactivity, or once per state change for cheaper).
 */
export function composeBase(
  base: ParamMap,
  stateOverrides: StateOverrides | undefined,
  moodOverrides: MoodOverrides | undefined,
  state: VoiceState,
  mood: Mood,
): ParamMap {
  const out: ParamMap = { ...base };

  // State delta — flat add (or replace, for string-typed fields).
  const stateDelta = stateOverrides?.[state];
  if (stateDelta) applyDelta(out, stateDelta, 1);

  // Mood delta — scaled by the current mood value on each dimension.
  // Skip near-zero values so <0.02 jitter doesn't churn uniforms.
  if (moodOverrides) {
    for (const dim of MOOD_DIMS) {
      const delta = moodOverrides[dim];
      const value = mood[dim];
      if (!delta) continue;
      if (Math.abs(value) < 0.02) continue;
      applyDelta(out, delta, value);
    }
  }

  return out;
}

/** Mutate `target` in place, adding numeric deltas (scaled by `weight`)
 * and replacing string/boolean values. Keys absent from `target` are
 * ignored — we never introduce new uniform names via a delta map.
 *
 * Uses `Object.hasOwn` (not `in`) so inherited prototype properties
 * can't sneak through the unknown-key filter. Matters here because
 * variant swaps mean the same override map may be composed against
 * different shaders' param sets over a session's lifetime. */
function applyDelta(target: ParamMap, delta: ParamMap, weight: number): void {
  for (const [k, v] of Object.entries(delta)) {
    if (!Object.hasOwn(target, k)) continue; // unknown uniform — drop
    if (typeof v === 'number' && typeof target[k] === 'number') {
      target[k] = (target[k] as number) + v * weight;
    } else if (typeof v === 'string') {
      target[k] = v;
    } else if (typeof v === 'boolean') {
      target[k] = v;
    }
  }
}
