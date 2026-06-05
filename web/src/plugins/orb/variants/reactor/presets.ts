/**
 * Reactor variant palettes — a kaleidoscopic KIFS fractal caged in the orb's
 * glass shell. `primaryEnergy` is the fractal's emissive tint (boosted by
 * `colorBoost`); `secondaryEnergy` tints the rim glint. Seeded from the
 * lil-gui prototype.
 */

export interface ReactorPreset {
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
  // --- reactor-specific (KIFS) ---
  foldSteps: number;
  kifsScale: number;
  kifsOffset: number;
  textureScale: number;
  brightness: number;
  colorBoost: number;
  lightSpeed: number;
  maxSteps: number;
  autoRotationSpeed: number;
  shellInner: number;
  bloom: number;
}

export const REACTOR_PRESETS: Record<string, ReactorPreset> = {
  // The prototype look — periwinkle fractal, cyan rim.
  Reactor: {
    primaryEnergy: '#7780ff', secondaryEnergy: '#00e5ff',
    density: 1.0, atmosphereGlow: 0.30, speed: 1.0, chromaticAberration: 0.012,
    asymmetry: 0.30, orbRotation: 0.25, dpr: 0.8,
    foldSteps: 6, kifsScale: 1.26, kifsOffset: 8.9, textureScale: 1.45,
    brightness: 1.42, colorBoost: 3.0, lightSpeed: 5.5, maxSteps: 110,
    autoRotationSpeed: 1.0, shellInner: 1.82, bloom: 1.0,
  },
  // Fusion — near-white, tight folds, fast glint.
  Fusion: {
    primaryEnergy: '#dbe4ff', secondaryEnergy: '#ffffff',
    density: 1.0, atmosphereGlow: 0.34, speed: 1.1, chromaticAberration: 0.016,
    asymmetry: 0.35, orbRotation: 0.30, dpr: 0.8,
    foldSteps: 7, kifsScale: 1.30, kifsOffset: 8.2, textureScale: 1.55,
    brightness: 1.30, colorBoost: 2.6, lightSpeed: 6.5, maxSteps: 110,
    autoRotationSpeed: 1.3, shellInner: 1.84, bloom: 1.1,
  },
  // Acid — green core, magenta rim, slow heavy folds.
  Acid: {
    primaryEnergy: '#7CFF6B', secondaryEnergy: '#ff3df0',
    density: 1.0, atmosphereGlow: 0.32, speed: 0.9, chromaticAberration: 0.018,
    asymmetry: 0.40, orbRotation: 0.22, dpr: 0.8,
    foldSteps: 5, kifsScale: 1.22, kifsOffset: 9.6, textureScale: 1.35,
    brightness: 1.55, colorBoost: 3.2, lightSpeed: 4.5, maxSteps: 110,
    autoRotationSpeed: 0.8, shellInner: 1.80, bloom: 1.0,
  },
  // Sunset — amber/pink, warm + dense.
  Sunset: {
    primaryEnergy: '#ffae42', secondaryEnergy: '#ff5d8f',
    density: 1.0, atmosphereGlow: 0.30, speed: 0.95, chromaticAberration: 0.014,
    asymmetry: 0.30, orbRotation: 0.24, dpr: 0.8,
    foldSteps: 6, kifsScale: 1.27, kifsOffset: 8.7, textureScale: 1.45,
    brightness: 1.45, colorBoost: 3.0, lightSpeed: 5.0, maxSteps: 110,
    autoRotationSpeed: 1.0, shellInner: 1.82, bloom: 0.95,
  },
  // Vapor — pink/cyan, dreamy + bright.
  Vapor: {
    primaryEnergy: '#ff6ec7', secondaryEnergy: '#5ef2ff',
    density: 1.0, atmosphereGlow: 0.36, speed: 1.05, chromaticAberration: 0.020,
    asymmetry: 0.45, orbRotation: 0.28, dpr: 0.8,
    foldSteps: 6, kifsScale: 1.28, kifsOffset: 8.5, textureScale: 1.50,
    brightness: 1.50, colorBoost: 3.0, lightSpeed: 5.8, maxSteps: 110,
    autoRotationSpeed: 1.1, shellInner: 1.83, bloom: 1.1,
  },
};

export type ReactorPaletteName = keyof typeof REACTOR_PRESETS;
