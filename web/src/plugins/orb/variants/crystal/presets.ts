/**
 * Crystal variant palettes. Shared base fields (required by
 * stateSnapshot()) + crystal-specific optical parameters.
 */

export interface CrystalPreset {
  // Shared (consumed by stateSnapshot + Atmosphere)
  primaryEnergy: string;
  secondaryEnergy: string;
  speed: number;
  density: number;              // repurposed: wobble amplitude
  dpr: number;
  atmosphereGlow: number;
  atmosphereLevel: number;
  atmosphereScale: number;
  orbRotation: number;
  asymmetry: number;
  chromaticAberration: number;
  // Crystal-specific
  transmission: number;         // 0-1 — how much light passes through
  ior: number;                  // 1.0-2.5 — index of refraction
  thickness: number;            // 0-2 — volumetric thickness
  roughness: number;            // 0-1 — frosted vs clear
  iridescence: number;          // 0-1 — thin-film shimmer
  detail: number;               // 0-3 — icosahedron subdivision
  envIntensity: number;         // 0-2 — HDRI reflection strength
}

export const CRYSTAL_PRESETS: Record<string, CrystalPreset> = {
  Prism: {
    primaryEnergy: '#a78bfa', secondaryEnergy: '#22d3ee',
    speed: 0.35, density: 0.6, dpr: 0.9,
    atmosphereGlow: 0.10, atmosphereLevel: 0.6, atmosphereScale: 1.04, orbRotation: 0.30,
    asymmetry: 0.30, chromaticAberration: 0.08,
    transmission: 1.0, ior: 1.6, thickness: 0.8, roughness: 0.02, iridescence: 0.5,
    detail: 0, envIntensity: 1.3,
  },
  Sapphire: {
    primaryEnergy: '#3b82f6', secondaryEnergy: '#7c3aed',
    speed: 0.30, density: 0.5, dpr: 0.9,
    atmosphereGlow: 0.12, atmosphereLevel: 0.7, atmosphereScale: 1.04, orbRotation: 0.25,
    asymmetry: 0.25, chromaticAberration: 0.04,
    transmission: 1.0, ior: 1.85, thickness: 0.9, roughness: 0.04, iridescence: 0.25,
    detail: 1, envIntensity: 1.1,
  },
  Topaz: {
    primaryEnergy: '#f59e0b', secondaryEnergy: '#dc2626',
    speed: 0.40, density: 0.7, dpr: 0.9,
    atmosphereGlow: 0.14, atmosphereLevel: 0.7, atmosphereScale: 1.03, orbRotation: 0.35,
    asymmetry: 0.30, chromaticAberration: 0.06,
    transmission: 0.95, ior: 1.7, thickness: 0.7, roughness: 0.06, iridescence: 0.15,
    detail: 1, envIntensity: 1.2,
  },
  Obsidian: {
    primaryEnergy: '#1e1b4b', secondaryEnergy: '#64748b',
    speed: 0.28, density: 0.4, dpr: 0.9,
    atmosphereGlow: 0.08, atmosphereLevel: 0.5, atmosphereScale: 1.03, orbRotation: 0.22,
    asymmetry: 0.15, chromaticAberration: 0.02,
    transmission: 0.4, ior: 1.5, thickness: 1.2, roughness: 0.35, iridescence: 0.0,
    detail: 0, envIntensity: 0.9,
  },
};
