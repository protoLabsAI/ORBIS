export interface NebulaPreset {
  primaryEnergy: string;
  secondaryEnergy: string;
  speed: number;
  density: number;              // beer-lambert sigma multiplier
  dpr: number;
  atmosphereGlow: number;
  atmosphereLevel: number;
  atmosphereScale: number;
  orbRotation: number;
  asymmetry: number;
  chromaticAberration: number;
  // Nebula-specific
  cloudScale: number;           // spatial frequency of the noise
  cloudiness: number;           // opacity curve multiplier
  drift: number;                // drift speed relative to uTime
  softness: number;             // edge fade-out width
  internalAnim: number;         // stays for schema parity; unused by shader
}

export const NEBULA_PRESETS: Record<string, NebulaPreset> = {
  Andromeda: {
    primaryEnergy: '#c084fc', secondaryEnergy: '#38bdf8',
    speed: 0.5, density: 1.3, dpr: 0.55,
    atmosphereGlow: 0.18, atmosphereLevel: 0.9, atmosphereScale: 1.04, orbRotation: 0.45,
    asymmetry: 0.4, chromaticAberration: 0.018,
    cloudScale: 1.8, cloudiness: 0.95, drift: 0.5, softness: 0.6, internalAnim: 0.4,
  },
  Emerald: {
    primaryEnergy: '#34d399', secondaryEnergy: '#f472b6',
    speed: 0.45, density: 1.5, dpr: 0.55,
    atmosphereGlow: 0.22, atmosphereLevel: 0.9, atmosphereScale: 1.04, orbRotation: 0.55,
    asymmetry: 0.5, chromaticAberration: 0.022,
    cloudScale: 2.1, cloudiness: 1.0, drift: 0.6, softness: 0.55, internalAnim: 0.45,
  },
  Helios: {
    primaryEnergy: '#fbbf24', secondaryEnergy: '#ef4444',
    speed: 0.6, density: 1.8, dpr: 0.55,
    atmosphereGlow: 0.28, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.6,
    asymmetry: 0.55, chromaticAberration: 0.028,
    cloudScale: 1.6, cloudiness: 1.1, drift: 0.7, softness: 0.5, internalAnim: 0.5,
  },
  Veil: {
    primaryEnergy: '#e4e4e7', secondaryEnergy: '#3730a3',
    speed: 0.35, density: 1.0, dpr: 0.55,
    atmosphereGlow: 0.12, atmosphereLevel: 0.8, atmosphereScale: 1.04, orbRotation: 0.4,
    asymmetry: 0.3, chromaticAberration: 0.012,
    cloudScale: 2.4, cloudiness: 0.8, drift: 0.4, softness: 0.7, internalAnim: 0.35,
  },
};
