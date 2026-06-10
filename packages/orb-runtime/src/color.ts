import * as THREE from 'three';
import { clamp01 } from './math';

/**
 * Shift a color's saturation / luminance while preserving hue.
 * Used by stateSnapshot() to derive per-state color variants from
 * a palette's primary/secondary.
 */
export function withHSL(hex: string, satMult: number, lumMult: number): THREE.Color {
  const c = new THREE.Color(hex);
  const hsl = { h: 0, s: 0, l: 0 };
  c.getHSL(hsl);
  return new THREE.Color().setHSL(
    hsl.h,
    clamp01(hsl.s * satMult),
    clamp01(hsl.l * lumMult),
  );
}
