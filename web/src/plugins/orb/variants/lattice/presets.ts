/**
 * Lattice variant palettes. Each preset carries the OrbBasePreset
 * fields (so useStateCrossfade can derive per-state snapshots) plus
 * the lattice-specific tunables (cube size, grid scale, distortion,
 * glow, RGB offsets feeding the cosine-palette shimmer).
 */

export interface LatticePreset {
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

  // Lattice-specific.
  cubeSize: number;       // half-extent of the AABB
  gridScale: number;      // spatial frequency of the lattice
  distortion: number;     // sin/cos warp on the cells
  glow: number;           // multiplier on the volume accumulator
  colorOffsetR: number;   // RGB shift in the cosine palette
  colorOffsetG: number;
  colorOffsetB: number;

  // Performance.
  dpr: number;
}

export const LATTICE_PRESETS: Record<string, LatticePreset> = {
  // ProtoLabs — brand scheme: lavender → indigo (#9b87f2 → #818cf8 → #6366f1).
  ProtoLabs: {
    primaryEnergy: '#9b87f2', secondaryEnergy: '#6366f1', density: 1.6,
    atmosphereGlow: 0.16, atmosphereLevel: 1.0, atmosphereScale: 1.03, speed: 0.5,
    chromaticAberration: 0.018, asymmetry: 0.45, orbRotation: 0.55, cubeSize: 2.0,
    gridScale: 1.0, distortion: 0.0, glow: 0.6, colorOffsetR: 2.0, colorOffsetG: 1.0,
    colorOffsetB: 0.0, dpr: 0.7,
  },
  // Default — cyan/rose, vivid grid lines through a tightly-packed cube.
  Glasshouse: {
    primaryEnergy: '#7dd3fc', secondaryEnergy: '#fb7185',
    density: 1.6, atmosphereGlow: 0.16, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.5, chromaticAberration: 0.018, asymmetry: 0.45, orbRotation: 0.55,
    cubeSize: 2.0, gridScale: 1.0, distortion: 0.0, glow: 0.6,
    colorOffsetR: 2.0, colorOffsetG: 1.0, colorOffsetB: 0.0,
    dpr: 0.7,
  },
  // Warm — amber/wine, slight distortion, brighter glow.
  Forge: {
    primaryEnergy: '#f59e0b', secondaryEnergy: '#7c2d12',
    density: 1.8, atmosphereGlow: 0.20, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.55, chromaticAberration: 0.024, asymmetry: 0.55, orbRotation: 0.60,
    cubeSize: 2.2, gridScale: 1.1, distortion: 0.15, glow: 0.7,
    colorOffsetR: 0.8, colorOffsetG: 0.2, colorOffsetB: 1.6,
    dpr: 0.7,
  },
  // Cool monochrome — silver/onyx, no distortion, clean lattice grid.
  Quartz: {
    primaryEnergy: '#cbd5e1', secondaryEnergy: '#0f172a',
    density: 1.2, atmosphereGlow: 0.14, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.4, chromaticAberration: 0.012, asymmetry: 0.30, orbRotation: 0.45,
    cubeSize: 2.0, gridScale: 0.9, distortion: 0.0, glow: 0.5,
    colorOffsetR: 0.0, colorOffsetG: 0.0, colorOffsetB: 0.0,
    dpr: 0.7,
  },
  // High-energy — lime/magenta, heavy distortion, dense grid.
  Voltage: {
    primaryEnergy: '#a3e635', secondaryEnergy: '#d946ef',
    density: 2.0, atmosphereGlow: 0.20, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.7, chromaticAberration: 0.026, asymmetry: 0.60, orbRotation: 0.75,
    cubeSize: 2.0, gridScale: 1.4, distortion: 0.35, glow: 0.85,
    colorOffsetR: 1.6, colorOffsetG: 0.6, colorOffsetB: 2.4,
    dpr: 0.7,
  },
};

export type LatticePaletteName = keyof typeof LATTICE_PRESETS;
