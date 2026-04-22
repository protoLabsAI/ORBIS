/**
 * Fractal variant palettes. Each pair places primary and secondary at
 * or near 180° on the color wheel so the secondary→primary gradient
 * reads as a clean contrast through the fractal shell rather than a
 * subtle hue shift.
 *
 * Ported verbatim from the original viz.js. These numeric values are
 * tuned by feel; changing them is a UX call.
 */

export interface FractalPreset {
  primaryEnergy: string;
  secondaryEnergy: string;
  speed: number;
  density: number;
  dpr: number;
  atmosphereGlow: number;
  atmosphereLevel: number;
  atmosphereScale: number;
  orbRotation: number;
  internalAnim: number;
  fractalIters: number;
  fractalScale: number;
  fractalDecay: number;
  smoothness: number;
  asymmetry: number;
  chromaticAberration: number;
}

export const FRACTAL_PRESETS: Record<string, FractalPreset> = {
  // Sky / pink — soft cool/warm complementary, reads as "calm tech."
  Aurora: {
    primaryEnergy: '#0ea5e9', secondaryEnergy: '#f472b6', speed: 0.5, density: 2.4, dpr: 0.7,
    atmosphereGlow: 0.18, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.80,
    internalAnim: 0.42, fractalIters: 4, fractalScale: 0.85, fractalDecay: -17.0,
    smoothness: 0.032, asymmetry: 0.50, chromaticAberration: 0.022,
  },
  // Orange / indigo — classic fire/ice complementary, high energy.
  Ember: {
    primaryEnergy: '#f97316', secondaryEnergy: '#4338ca', speed: 0.6, density: 2.1, dpr: 0.7,
    atmosphereGlow: 0.22, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.70,
    internalAnim: 0.55, fractalIters: 4, fractalScale: 0.90, fractalDecay: -15.5,
    smoothness: 0.028, asymmetry: 0.60, chromaticAberration: 0.026,
  },
  // Gold / violet — pure complementary on the yellow/violet axis.
  Citrus: {
    primaryEnergy: '#eab308', secondaryEnergy: '#a855f7', speed: 0.55, density: 1.8, dpr: 0.7,
    atmosphereGlow: 0.18, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.55,
    internalAnim: 0.45, fractalIters: 3, fractalScale: 0.78, fractalDecay: -14.8,
    smoothness: 0.014, asymmetry: 0.38, chromaticAberration: 0.022,
  },
  // Emerald / rose — green/magenta complementary, lush + punchy.
  Forest: {
    primaryEnergy: '#10b981', secondaryEnergy: '#db2777', speed: 0.9, density: 1.6, dpr: 0.7,
    atmosphereGlow: 0.20, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.60,
    internalAnim: 0.42, fractalIters: 4, fractalScale: 0.86, fractalDecay: -22.0,
    smoothness: 0.060, asymmetry: 0.30, chromaticAberration: 0.006,
  },
  // Off-white / near-black — minimal monochrome.
  Noir: {
    primaryEnergy: '#e4e4e7', secondaryEnergy: '#18181b', speed: 0.35, density: 1.0, dpr: 0.7,
    atmosphereGlow: 0.14, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.45,
    internalAnim: 0.22, fractalIters: 4, fractalScale: 0.76, fractalDecay: -20.0,
    smoothness: 0.034, asymmetry: 0.10, chromaticAberration: 0.016,
  },
};

export type FractalPaletteName = keyof typeof FRACTAL_PRESETS;
