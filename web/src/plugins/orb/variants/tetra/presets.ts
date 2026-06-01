/**
 * Tetra variant palettes. Each preset carries the OrbBasePreset
 * fields useStateCrossfade reads (density, atmosphereGlow, speed,
 * chromaticAberration, asymmetry, orbRotation, primary/secondary)
 * plus the tetra-specific tunables that drive the fold loop and
 * glow accumulator.
 */

export interface TetraPreset {
  // OrbBasePreset shape — drives state-crossfade snapshots.
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

  // Tetra-specific.
  shapeSize: number;
  iterations: number;
  foldX: number;
  foldY: number;
  foldZ: number;
  glowIntensity: number;
  glowBase: number;
  internalAnim: number;

  // Performance.
  dpr: number;
}

export const TETRA_PRESETS: Record<string, TetraPreset> = {
  // ProtoLabs — brand scheme: lavender → indigo (#9b87f2 → #818cf8 → #6366f1).
  ProtoLabs: {
    primaryEnergy: '#9b87f2', secondaryEnergy: '#6366f1', density: 1.6,
    atmosphereGlow: 0.16, atmosphereLevel: 1.0, atmosphereScale: 1.03, speed: 0.5,
    chromaticAberration: 0.018, asymmetry: 0.45, orbRotation: 0.55, shapeSize: 1.6,
    iterations: 5, foldX: 1.7, foldY: 0.5, foldZ: 0.7, glowIntensity: 0.030,
    glowBase: 0.28, internalAnim: 0.15, dpr: 0.7,
  },
  // Default — sky/rose, balanced fold, the look from the source shader.
  Drift: {
    primaryEnergy: '#7dd3fc', secondaryEnergy: '#fb7185',
    density: 1.6, atmosphereGlow: 0.16, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.5, chromaticAberration: 0.018, asymmetry: 0.45, orbRotation: 0.55,
    shapeSize: 1.6, iterations: 5, foldX: 1.7, foldY: 0.5, foldZ: 0.7,
    glowIntensity: 0.030, glowBase: 0.28, internalAnim: 0.15,
    dpr: 0.7,
  },
  // Hot — fire/cobalt, higher iterations, more fold chaos.
  Forge: {
    primaryEnergy: '#f97316', secondaryEnergy: '#1e3a8a',
    density: 1.8, atmosphereGlow: 0.22, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.6, chromaticAberration: 0.024, asymmetry: 0.55, orbRotation: 0.65,
    shapeSize: 1.5, iterations: 6, foldX: 1.9, foldY: 0.6, foldZ: 0.8,
    glowIntensity: 0.034, glowBase: 0.32, internalAnim: 0.18,
    dpr: 0.7,
  },
  // Cool monochrome — silver/onyx, low iterations for cleaner shape.
  Steel: {
    primaryEnergy: '#cbd5e1', secondaryEnergy: '#0f172a',
    density: 1.2, atmosphereGlow: 0.14, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.4, chromaticAberration: 0.012, asymmetry: 0.35, orbRotation: 0.50,
    shapeSize: 1.7, iterations: 4, foldX: 1.6, foldY: 0.4, foldZ: 0.6,
    glowIntensity: 0.022, glowBase: 0.25, internalAnim: 0.12,
    dpr: 0.7,
  },
  // High-energy — lime/magenta, dense fold chaos for the dancing look.
  Pulse: {
    primaryEnergy: '#a3e635', secondaryEnergy: '#d946ef',
    density: 2.0, atmosphereGlow: 0.20, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.7, chromaticAberration: 0.026, asymmetry: 0.60, orbRotation: 0.75,
    shapeSize: 1.4, iterations: 7, foldX: 2.0, foldY: 0.55, foldZ: 0.85,
    glowIntensity: 0.036, glowBase: 0.30, internalAnim: 0.22,
    dpr: 0.7,
  },
};

export type TetraPaletteName = keyof typeof TETRA_PRESETS;
