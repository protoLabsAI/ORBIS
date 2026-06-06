import type { OrbBasePreset } from '../../shared/stateSnapshot';

/**
 * Edison variant palettes.
 *
 * A glass capsule housing writhing filament "tentacles" of light with
 * particles streaming along them — a living Edison bulb. `primaryEnergy` is
 * the bright core filament; `secondaryEnergy` is the outer glow / glass tint.
 * The shared state-snapshot crossfades these per voice state (dimmer at idle,
 * full at speaking).
 *
 * Values seeded from the original lil-gui prototype, scaled to orb framing.
 */

export interface EdisonPreset extends OrbBasePreset {
  // --- shared base (read by stateSnapshot) ---
  primaryEnergy: string; // core filament
  secondaryEnergy: string; // glow / glass tint
  density: number;
  atmosphereGlow: number; // drives bloom intensity
  speed: number;
  chromaticAberration: number;
  asymmetry: number;
  orbRotation: number;
  dpr: number;
  // --- Edison-specific dynamics ---
  waveSpeed: number; // writhing speed
  curlFrequency: number; // coil tightness along the filament
  writhingAmplitude: number; // lateral sway
  tentacleCount: number; // filament bundles
  filaments: number; // glow strands per bundle
  particleCount: number;
  particleSize: number;
  particleSpeed: number; // flow rate along filaments
  particleSpread: number; // orbit radius around a filament
  capsuleOpacity: number; // glass envelope
  bloom: number; // bloom strength
}

export const EDISON_PRESETS: Record<string, EdisonPreset> = {
  // Classic tungsten — warm gold filament, golden glass. The prototype look.
  Edison: {
    primaryEnergy: '#ffd500', secondaryEnergy: '#ffbf52',
    density: 1.5, atmosphereGlow: 0.30, speed: 1.0, chromaticAberration: 0.010,
    asymmetry: 0.35, orbRotation: 0.28, dpr: 0.8,
    waveSpeed: 1.4, curlFrequency: 0.26, writhingAmplitude: 1.4,
    tentacleCount: 8, filaments: 12, particleCount: 1800, particleSize: 0.05,
    particleSpeed: 0.74, particleSpread: 0.33, capsuleOpacity: 0.05, bloom: 0.9,
  },
  // Tungsten white — a hotter, near-white filament.
  Filament: {
    primaryEnergy: '#fff1c9', secondaryEnergy: '#ffca74',
    density: 1.4, atmosphereGlow: 0.34, speed: 0.9, chromaticAberration: 0.008,
    asymmetry: 0.28, orbRotation: 0.24, dpr: 0.8,
    waveSpeed: 1.1, curlFrequency: 0.22, writhingAmplitude: 1.2,
    tentacleCount: 7, filaments: 14, particleCount: 2000, particleSize: 0.045,
    particleSpeed: 0.6, particleSpread: 0.30, capsuleOpacity: 0.05, bloom: 1.0,
  },
  // Cold plasma — electric blue/indigo, faster + tighter coils.
  Plasma: {
    primaryEnergy: '#7dd3fc', secondaryEnergy: '#818cf8',
    density: 1.6, atmosphereGlow: 0.32, speed: 1.2, chromaticAberration: 0.014,
    asymmetry: 0.45, orbRotation: 0.30, dpr: 0.8,
    waveSpeed: 1.9, curlFrequency: 0.32, writhingAmplitude: 1.5,
    tentacleCount: 9, filaments: 12, particleCount: 2200, particleSize: 0.042,
    particleSpeed: 1.0, particleSpread: 0.36, capsuleOpacity: 0.045, bloom: 1.0,
  },
  // Neon — magenta core, cyan glow; loud sign-tube energy.
  Neon: {
    primaryEnergy: '#ff5db1', secondaryEnergy: '#00e5ff',
    density: 1.6, atmosphereGlow: 0.38, speed: 1.15, chromaticAberration: 0.018,
    asymmetry: 0.5, orbRotation: 0.32, dpr: 0.8,
    waveSpeed: 1.7, curlFrequency: 0.30, writhingAmplitude: 1.6,
    tentacleCount: 8, filaments: 13, particleCount: 2000, particleSize: 0.046,
    particleSpeed: 0.9, particleSpread: 0.38, capsuleOpacity: 0.05, bloom: 1.1,
  },
  // Ember — deep red-orange, slow + heavy, low coil.
  Ember: {
    primaryEnergy: '#ff7a18', secondaryEnergy: '#c81d25',
    density: 1.4, atmosphereGlow: 0.30, speed: 0.8, chromaticAberration: 0.012,
    asymmetry: 0.3, orbRotation: 0.22, dpr: 0.8,
    waveSpeed: 1.0, curlFrequency: 0.20, writhingAmplitude: 1.3,
    tentacleCount: 7, filaments: 12, particleCount: 1500, particleSize: 0.052,
    particleSpeed: 0.55, particleSpread: 0.30, capsuleOpacity: 0.06, bloom: 0.85,
  },
};

export type EdisonPaletteName = keyof typeof EDISON_PRESETS;
