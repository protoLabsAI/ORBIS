/**
 * Spectrum variant palettes. Each preset carries the OrbBasePreset
 * shape (used by useStateCrossfade) plus the spectrum-specific
 * tunables (fade radii, smoothing, RGBA phase offsets feeding the
 * cosine rainbow palette).
 */

export interface SpectrumPreset {
  // OrbBasePreset shape — used by useStateCrossfade.
  primaryEnergy: string;
  secondaryEnergy: string;
  density: number;
  atmosphereGlow: number;
  atmosphereLevel: number;
  atmosphereScale: number;
  speed: number;
  chromaticAberration: number;
  asymmetry: number;
  orbRotation: number;

  // Spectrum-specific.
  fractalScale: number;     // shrinks/expands SDF coordinate space
  fadeOuter: number;        // soft-fade outer radius (orb silhouette)
  fadeInner: number;        // soft-fade inner radius (full contribution)
  smoothing: number;        // smooth-min blend k between two spheres
  glow: number;             // multiplier on the volume accumulator
  colorPhaseR: number;      // RGBA phase offsets for the cosine palette
  colorPhaseG: number;
  colorPhaseB: number;
  colorPhaseA: number;

  // Performance.
  dpr: number;
}

export const SPECTRUM_PRESETS: Record<string, SpectrumPreset> = {
  // Default — full rainbow shimmer through the volume.
  Rainbow: {
    primaryEnergy: '#a78bfa', secondaryEnergy: '#f472b6',
    density: 1.6, atmosphereGlow: 0.18, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.5, chromaticAberration: 0.018, asymmetry: 0.45, orbRotation: 0.50,
    fractalScale: 1.0, fadeOuter: 2.56, fadeInner: 2.55, smoothing: 1.55, glow: 1.0,
    colorPhaseR: 5.8, colorPhaseG: 4.1, colorPhaseB: 2.8, colorPhaseA: 0.2,
    dpr: 0.7,
  },
  // Warm — phases shifted so the rainbow tilts toward sunset hues.
  Sunset: {
    primaryEnergy: '#f97316', secondaryEnergy: '#7c2d12',
    density: 1.8, atmosphereGlow: 0.22, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.55, chromaticAberration: 0.024, asymmetry: 0.55, orbRotation: 0.55,
    fractalScale: 1.0, fadeOuter: 2.6, fadeInner: 2.4, smoothing: 1.6, glow: 1.1,
    colorPhaseR: 0.5, colorPhaseG: 1.5, colorPhaseB: 3.0, colorPhaseA: 0.5,
    dpr: 0.7,
  },
  // Cool — blue/cyan phase, smaller outer radius for a tighter halo.
  Tide: {
    primaryEnergy: '#0ea5e9', secondaryEnergy: '#1e3a8a',
    density: 1.4, atmosphereGlow: 0.16, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.45, chromaticAberration: 0.014, asymmetry: 0.40, orbRotation: 0.45,
    fractalScale: 1.05, fadeOuter: 2.4, fadeInner: 2.2, smoothing: 1.4, glow: 0.9,
    colorPhaseR: 3.0, colorPhaseG: 5.0, colorPhaseB: 6.5, colorPhaseA: 1.5,
    dpr: 0.7,
  },
  // Soft halo — wide fade band (outer 3.0, inner 2.0), low contrast palette.
  Halo: {
    primaryEnergy: '#fef3c7', secondaryEnergy: '#a78bfa',
    density: 1.2, atmosphereGlow: 0.14, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.4, chromaticAberration: 0.012, asymmetry: 0.30, orbRotation: 0.40,
    fractalScale: 0.95, fadeOuter: 3.0, fadeInner: 2.0, smoothing: 1.7, glow: 0.85,
    colorPhaseR: 2.0, colorPhaseG: 2.5, colorPhaseB: 3.0, colorPhaseA: 0.3,
    dpr: 0.7,
  },
};

export type SpectrumPaletteName = keyof typeof SPECTRUM_PRESETS;
