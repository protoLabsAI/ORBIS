/**
 * Default mood→uniform mappings shared across the orb family.
 *
 * Variants register their own `moodDefaults` against the registry,
 * but most of the body is universal: every variant's preset extends
 * `OrbBasePreset` so the same valence/arousal/guardedness deltas
 * applied to atmosphereGlow / density / speed / orbRotation /
 * asymmetry produce visible motion across all five.
 *
 * Variant-specific tunables (fractalIters, cloudiness, hueSpread,
 * glowIntensity, etc.) layer on top of these in each variant's
 * `index.tsx` registration.
 *
 * Numeric semantics, per compose.ts:
 *   numericDelta * mood[dim]  →  added to the base preset value
 *
 * So a delta of `+0.06` on atmosphereGlow with valence at +0.5 adds
 * +0.03; with valence at -0.5 it subtracts 0.03. Symmetric for valence
 * and arousal (range [-1, +1]). Guardedness is [0, +1] so its deltas
 * only ever push in one direction.
 *
 * Tuned values are conservative enough that even a fully-pinned mood
 * preview (mood = 1.0) doesn't push uniforms outside their schema
 * range — clamping happens at shader-uniform time anyway, but staying
 * within range keeps the visual coherent.
 */

import type { MoodOverrides } from '../compose';

/** Mood deltas keyed only on `OrbBasePreset` fields — usable on every
 * variant since the base shape is shared. */
export const SHARED_MOOD_DEFAULTS: MoodOverrides = {
  // Positive valence = warmer, more open, slightly brighter halo.
  // Negative valence reverses the sign (dimmer, more guarded shape).
  valence: {
    atmosphereGlow: 0.06,
    density: 0.15,
    asymmetry: -0.05,
  },
  // High arousal = faster, denser, more rotation.
  // Low arousal slows everything for the calm-state look.
  arousal: {
    speed: 0.4,
    orbRotation: 0.20,
    density: 0.40,
    chromaticAberration: 0.008,
  },
  // High guardedness = closed up: dim halo, less density, more
  // asymmetric (cagey-feeling). Range [0, +1] so it never brightens.
  guardedness: {
    atmosphereGlow: -0.10,
    density: -0.40,
    asymmetry: 0.15,
    orbRotation: -0.15,
  },
};

/** Merge shared defaults with variant-specific extensions. Variant
 * keys win per-dimension; merge is per-key inside each dimension so
 * a variant adding `glowIntensity` to its valence map keeps the
 * shared atmosphereGlow/density/asymmetry deltas. */
export function withSharedMoodDefaults(extra: MoodOverrides): MoodOverrides {
  const out: MoodOverrides = {};
  for (const dim of ['valence', 'arousal', 'guardedness'] as const) {
    const s = SHARED_MOOD_DEFAULTS[dim];
    const e = extra[dim];
    if (!s && !e) continue;
    out[dim] = { ...(s ?? {}), ...(e ?? {}) };
  }
  return out;
}
