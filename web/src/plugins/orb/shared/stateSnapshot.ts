import * as THREE from 'three';
import { lerp } from './math';
import { withHSL } from './color';
import type { VoiceState } from '../../../voice/state';

/**
 * Per-state target snapshot. These are the "resting" uniform values
 * for a given state; audio-driven modulation is added on top during
 * render. The crossfade lerps between two snapshots over STATE_XFADE_MS.
 */
export interface StateSnapshot {
  density: number;
  glow: number;
  speed: number;
  ca: number;
  asymmetry: number;
  rotation: number;
  scale: number;
  primary: THREE.Color;
  secondary: THREE.Color;
}

/** A palette's resting values — read by stateSnapshot() to derive per-state values. */
export interface OrbBasePreset {
  density: number;
  atmosphereGlow: number;
  speed: number;
  chromaticAberration: number;
  asymmetry: number;
  orbRotation: number;
  primaryEnergy: string;
  secondaryEnergy: string;
}

export function stateSnapshot(state: VoiceState, base: OrbBasePreset): StateSnapshot {
  switch (state) {
    case 'idle':
      return {
        density: base.density * 0.55,
        glow: base.atmosphereGlow * 0.7,
        speed: base.speed * 0.7,
        ca: base.chromaticAberration * 0.4,
        asymmetry: base.asymmetry * 0.8,
        rotation: base.orbRotation * 0.55,
        scale: 0.94,
        primary: withHSL(base.primaryEnergy, 0.80, 0.70),
        secondary: withHSL(base.secondaryEnergy, 0.80, 0.70),
      };
    case 'listening':
      // Small inward pull + slightly cooler; the orb "takes in" but stays alive.
      return {
        density: base.density * 0.80,
        glow: base.atmosphereGlow * 0.85,
        speed: base.speed * 0.65,
        ca: base.chromaticAberration * 0.6,
        asymmetry: base.asymmetry * 0.85,
        rotation: base.orbRotation * 0.55,
        scale: 0.93,
        primary: withHSL(base.primaryEnergy, 0.95, 0.95),
        secondary: withHSL(base.secondaryEnergy, 0.95, 0.95),
      };
    case 'thinking':
      // Neither cool nor warm; slightly faster internal swirl suggesting work.
      return {
        density: base.density * 0.70,
        glow: base.atmosphereGlow * 0.90,
        speed: base.speed * 1.0,
        ca: base.chromaticAberration * 0.7,
        asymmetry: base.asymmetry * 0.9,
        rotation: base.orbRotation * 0.85,
        scale: 0.96,
        primary: withHSL(base.primaryEnergy, 0.95, 0.90),
        secondary: withHSL(base.secondaryEnergy, 0.95, 0.90),
      };
    case 'speaking':
      // Push outward, full saturation/luminance; audio modulation on top.
      return {
        density: base.density * 0.90,
        glow: base.atmosphereGlow * 1.10,
        speed: base.speed * 1.05,
        ca: base.chromaticAberration * 1.0,
        asymmetry: base.asymmetry,
        rotation: base.orbRotation * 1.0,
        scale: 1.06,
        primary: new THREE.Color(base.primaryEnergy),
        secondary: new THREE.Color(base.secondaryEnergy),
      };
  }
}

export function lerpSnapshot(a: StateSnapshot, b: StateSnapshot, t: number): StateSnapshot {
  return {
    density: lerp(a.density, b.density, t),
    glow: lerp(a.glow, b.glow, t),
    speed: lerp(a.speed, b.speed, t),
    ca: lerp(a.ca, b.ca, t),
    asymmetry: lerp(a.asymmetry, b.asymmetry, t),
    rotation: lerp(a.rotation, b.rotation, t),
    scale: lerp(a.scale, b.scale, t),
    primary: a.primary.clone().lerp(b.primary, t),
    secondary: a.secondary.clone().lerp(b.secondary, t),
  };
}
