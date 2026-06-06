import type { OrbBasePreset } from '../../shared/stateSnapshot';

/**
 * Flux variant palettes — a fluid, pulsing volumetric SDF fractal glowing
 * inside the orb's shell. The emission colour slowly cycles between the three
 * palette colours (`primaryEnergy` → `secondaryEnergy` → `tertiaryEnergy`)
 * rather than spinning hue continuously. Seeded from the "quantum orb" prototype.
 */

export interface FluxPreset extends OrbBasePreset {
  // --- shared base (read by stateSnapshot) ---
  primaryEnergy: string;
  secondaryEnergy: string;
  tertiaryEnergy: string;
  density: number;
  atmosphereGlow: number;
  speed: number;
  chromaticAberration: number;
  asymmetry: number;
  orbRotation: number;
  dpr: number;
  // --- flux-specific (SDF glow) ---
  iterations: number;
  distortion: number;
  pulseIntensity: number;
  fractalScale: number;
  brightness: number;
  cycleSpeed: number;
  bloom: number;
}

export const FLUX_PRESETS: Record<string, FluxPreset> = {
  // The prototype look — molten orange → magenta → violet, slow cycle.
  Flux: {
    primaryEnergy: '#ff7a18', secondaryEnergy: '#ff2d76', tertiaryEnergy: '#7a2dff',
    density: 1.0, atmosphereGlow: 0.30, speed: 1.0, chromaticAberration: 0.014,
    asymmetry: 0.30, orbRotation: 0.18, dpr: 0.8,
    iterations: 20, distortion: 2.2, pulseIntensity: 2.5, fractalScale: 0.16,
    brightness: 0.5, cycleSpeed: 0.14, bloom: 1.0,
  },
  // Ember — red/orange/gold, dense + warm.
  Ember: {
    primaryEnergy: '#ff3b1f', secondaryEnergy: '#ff8c1a', tertiaryEnergy: '#ffd24a',
    density: 1.0, atmosphereGlow: 0.28, speed: 0.9, chromaticAberration: 0.012,
    asymmetry: 0.25, orbRotation: 0.16, dpr: 0.8,
    iterations: 18, distortion: 2.0, pulseIntensity: 3.0, fractalScale: 0.15,
    brightness: 0.5, cycleSpeed: 0.10, bloom: 0.95,
  },
  // Tide — teal/cyan/blue, slow + cool.
  Tide: {
    primaryEnergy: '#16f0c8', secondaryEnergy: '#19a7ff', tertiaryEnergy: '#5d6bff',
    density: 1.0, atmosphereGlow: 0.32, speed: 0.95, chromaticAberration: 0.014,
    asymmetry: 0.30, orbRotation: 0.18, dpr: 0.8,
    iterations: 20, distortion: 2.3, pulseIntensity: 2.2, fractalScale: 0.17,
    brightness: 0.5, cycleSpeed: 0.12, bloom: 1.05,
  },
  // Toxic — acid green/lime/chartreuse, fast cycle.
  Toxic: {
    primaryEnergy: '#7CFF2A', secondaryEnergy: '#c8ff19', tertiaryEnergy: '#19ffa3',
    density: 1.0, atmosphereGlow: 0.30, speed: 1.05, chromaticAberration: 0.018,
    asymmetry: 0.35, orbRotation: 0.20, dpr: 0.8,
    iterations: 22, distortion: 2.4, pulseIntensity: 2.8, fractalScale: 0.18,
    brightness: 0.48, cycleSpeed: 0.20, bloom: 1.0,
  },
  // Nova — white/blue/pink, bright + dreamy.
  Nova: {
    primaryEnergy: '#dbe4ff', secondaryEnergy: '#5ec8ff', tertiaryEnergy: '#ff7ad6',
    density: 1.0, atmosphereGlow: 0.36, speed: 1.0, chromaticAberration: 0.020,
    asymmetry: 0.40, orbRotation: 0.18, dpr: 0.8,
    iterations: 20, distortion: 2.2, pulseIntensity: 2.4, fractalScale: 0.16,
    brightness: 0.55, cycleSpeed: 0.16, bloom: 1.1,
  },
};

export type FluxPaletteName = keyof typeof FLUX_PRESETS;
