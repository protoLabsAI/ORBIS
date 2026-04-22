export interface ParticlesPreset {
  primaryEnergy: string;
  secondaryEnergy: string;
  speed: number;
  density: number;              // radial push amplitude
  dpr: number;
  atmosphereGlow: number;
  atmosphereLevel: number;
  atmosphereScale: number;
  orbRotation: number;
  asymmetry: number;
  chromaticAberration: number;
  // Particles-specific
  count: number;                // 500–3000
  particleSize: number;         // 0.01–0.05
  jitter: number;               // 0–0.3 — user-voice chaos
  radius: number;               // 0.8–1.5 — base sphere radius
  hueSpread: number;            // 0–60 degrees — per-particle hue variance
}

export const PARTICLES_PRESETS: Record<string, ParticlesPreset> = {
  Constellation: {
    primaryEnergy: '#38bdf8', secondaryEnergy: '#fb923c',
    speed: 0.4, density: 0.6, dpr: 0.9,
    atmosphereGlow: 0.15, atmosphereLevel: 0.7, atmosphereScale: 1.05, orbRotation: 0.35,
    asymmetry: 0.25, chromaticAberration: 0.016,
    count: 1800, particleSize: 0.022, jitter: 0.08, radius: 1.15, hueSpread: 15,
  },
  Stardust: {
    primaryEnergy: '#f472b6', secondaryEnergy: '#a78bfa',
    speed: 0.35, density: 0.7, dpr: 0.9,
    atmosphereGlow: 0.22, atmosphereLevel: 0.8, atmosphereScale: 1.05, orbRotation: 0.30,
    asymmetry: 0.30, chromaticAberration: 0.020,
    count: 2400, particleSize: 0.018, jitter: 0.10, radius: 1.10, hueSpread: 22,
  },
  Ember: {
    primaryEnergy: '#fbbf24', secondaryEnergy: '#dc2626',
    speed: 0.55, density: 0.8, dpr: 0.9,
    atmosphereGlow: 0.30, atmosphereLevel: 0.8, atmosphereScale: 1.04, orbRotation: 0.40,
    asymmetry: 0.40, chromaticAberration: 0.024,
    count: 1500, particleSize: 0.026, jitter: 0.14, radius: 1.05, hueSpread: 18,
  },
  Moss: {
    primaryEnergy: '#4ade80', secondaryEnergy: '#0ea5e9',
    speed: 0.30, density: 0.5, dpr: 0.9,
    atmosphereGlow: 0.12, atmosphereLevel: 0.65, atmosphereScale: 1.06, orbRotation: 0.28,
    asymmetry: 0.20, chromaticAberration: 0.010,
    count: 2000, particleSize: 0.020, jitter: 0.06, radius: 1.20, hueSpread: 10,
  },
};
