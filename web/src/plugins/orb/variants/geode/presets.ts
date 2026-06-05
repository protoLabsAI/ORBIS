/**
 * Geode variant palettes — a glowing octahedron of volumetric plasma with neon
 * wireframe edges. `primaryEnergy` tints the plasma, `secondaryEnergy` the
 * wireframe frame. A near-white plasma lets the prototype's iridescent base
 * oscillation show through; saturated plasma colours tint it. Seeded from the
 * "octa plasma" prototype.
 */

export interface GeodePreset {
  // --- shared base (read by stateSnapshot) ---
  primaryEnergy: string;
  secondaryEnergy: string;
  density: number;
  atmosphereGlow: number;
  speed: number;
  chromaticAberration: number;
  asymmetry: number;
  orbRotation: number;
  dpr: number;
  // --- geode-specific ---
  shapeSize: number;
  shapeStretch: number;
  plasmaDensity: number;
  plasmaScale: number;
  brightness: number;
  shellInner: number;
  maxSteps: number;
  bloom: number;
}

export const GEODE_PRESETS: Record<string, GeodePreset> = {
  // The prototype look — icy near-white plasma (iridescent), white frame.
  Geode: {
    primaryEnergy: '#dfeaff', secondaryEnergy: '#eef1ff',
    density: 1.0, atmosphereGlow: 0.30, speed: 1.0, chromaticAberration: 0.012,
    asymmetry: 0.30, orbRotation: 0.20, dpr: 0.8,
    shapeSize: 1.6, shapeStretch: 0.7, plasmaDensity: 0.05, plasmaScale: 3.0,
    brightness: 1.0, shellInner: 1.9, maxSteps: 140, bloom: 1.0,
  },
  // Amethyst — purple plasma, pale-violet frame.
  Amethyst: {
    primaryEnergy: '#b266ff', secondaryEnergy: '#e9d5ff',
    density: 1.0, atmosphereGlow: 0.30, speed: 0.95, chromaticAberration: 0.014,
    asymmetry: 0.32, orbRotation: 0.18, dpr: 0.8,
    shapeSize: 1.55, shapeStretch: 0.66, plasmaDensity: 0.055, plasmaScale: 3.0,
    brightness: 1.05, shellInner: 1.9, maxSteps: 140, bloom: 1.05,
  },
  // Emerald — green plasma, mint frame.
  Emerald: {
    primaryEnergy: '#19ffa3', secondaryEnergy: '#d1fae5',
    density: 1.0, atmosphereGlow: 0.30, speed: 1.0, chromaticAberration: 0.013,
    asymmetry: 0.30, orbRotation: 0.20, dpr: 0.8,
    shapeSize: 1.6, shapeStretch: 0.72, plasmaDensity: 0.05, plasmaScale: 3.2,
    brightness: 1.0, shellInner: 1.9, maxSteps: 140, bloom: 1.0,
  },
  // Citrine — gold plasma, pale-gold frame, taller cut.
  Citrine: {
    primaryEnergy: '#ffd24a', secondaryEnergy: '#fff4cc',
    density: 1.0, atmosphereGlow: 0.28, speed: 0.95, chromaticAberration: 0.012,
    asymmetry: 0.28, orbRotation: 0.18, dpr: 0.8,
    shapeSize: 1.5, shapeStretch: 0.6, plasmaDensity: 0.05, plasmaScale: 3.0,
    brightness: 1.05, shellInner: 1.9, maxSteps: 140, bloom: 0.95,
  },
  // Sapphire — blue plasma, pale-blue frame.
  Sapphire: {
    primaryEnergy: '#4a7bff', secondaryEnergy: '#cfe0ff',
    density: 1.0, atmosphereGlow: 0.32, speed: 1.0, chromaticAberration: 0.015,
    asymmetry: 0.34, orbRotation: 0.20, dpr: 0.8,
    shapeSize: 1.6, shapeStretch: 0.7, plasmaDensity: 0.052, plasmaScale: 3.0,
    brightness: 1.05, shellInner: 1.9, maxSteps: 140, bloom: 1.05,
  },
};

export type GeodePaletteName = keyof typeof GEODE_PRESETS;
