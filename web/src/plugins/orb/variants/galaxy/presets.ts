/**
 * Galaxy variant palettes. OrbBasePreset shape + galaxy-specific
 * tunables (3-stop plasma palette, shell colour, plasma noise params,
 * voice-tint mix strength, particle count).
 */

export interface GalaxyPreset {
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

  // Galaxy-specific.
  // Plasma (the noise-driven gas inside).
  plasmaScale: number;       // texture frequency
  plasmaBrightness: number;  // multiplier on final colour
  voidThreshold: number;     // alpha smoothstep low edge
  colorDeep: string;         // 3-stop palette: deep / mid / bright
  colorMid: string;
  colorBright: string;
  voiceMix: number;          // 0..1 — how much primary/secondary tugs the palette

  // Shell (glass envelope).
  shellColor: string;
  shellOpacity: number;

  // Particles (dust field).
  particleCount: number;     // 100..1500
  particleColor: string;
  particleRadius: number;    // sphere of distribution

  // Performance.
  dpr: number;
}

export const GALAXY_PRESETS: Record<string, GalaxyPreset> = {
  // Default — cyan/teal galaxy with bright cores.
  Andromeda: {
    primaryEnergy: '#22d3ee', secondaryEnergy: '#1e1b4b',
    density: 1.5, atmosphereGlow: 0.18, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.5, chromaticAberration: 0.014, asymmetry: 0.40, orbRotation: 0.50,
    plasmaScale: 0.2, plasmaBrightness: 1.31, voidThreshold: 0.09,
    colorDeep: '#001433', colorMid: '#0084ff', colorBright: '#00ffe1',
    voiceMix: 0.25,
    shellColor: '#0066ff', shellOpacity: 0.41,
    particleCount: 600, particleColor: '#ffffff', particleRadius: 0.95,
    dpr: 0.7,
  },
  // Warm — sunset palette, denser plasma.
  Phoenix: {
    primaryEnergy: '#fbbf24', secondaryEnergy: '#7c2d12',
    density: 1.7, atmosphereGlow: 0.22, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.55, chromaticAberration: 0.020, asymmetry: 0.50, orbRotation: 0.55,
    plasmaScale: 0.22, plasmaBrightness: 1.5, voidThreshold: 0.07,
    colorDeep: '#3b0a02', colorMid: '#dc2626', colorBright: '#fbbf24',
    voiceMix: 0.30,
    shellColor: '#ff6b35', shellOpacity: 0.45,
    particleCount: 700, particleColor: '#ffe4a3', particleRadius: 0.95,
    dpr: 0.7,
  },
  // Violet/magenta — dramatic deep-space feel.
  Magellanic: {
    primaryEnergy: '#c084fc', secondaryEnergy: '#1e1b4b',
    density: 1.6, atmosphereGlow: 0.20, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.5, chromaticAberration: 0.018, asymmetry: 0.45, orbRotation: 0.50,
    plasmaScale: 0.18, plasmaBrightness: 1.4, voidThreshold: 0.10,
    colorDeep: '#1e1b4b', colorMid: '#7c3aed', colorBright: '#f0abfc',
    voiceMix: 0.25,
    shellColor: '#8b5cf6', shellOpacity: 0.42,
    particleCount: 600, particleColor: '#fce7f3', particleRadius: 0.95,
    dpr: 0.7,
  },
  // Quiet — sparse particles, low brightness, gentle palette.
  Calm: {
    primaryEnergy: '#94a3b8', secondaryEnergy: '#0f172a',
    density: 1.2, atmosphereGlow: 0.14, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.4, chromaticAberration: 0.010, asymmetry: 0.30, orbRotation: 0.40,
    plasmaScale: 0.15, plasmaBrightness: 1.1, voidThreshold: 0.12,
    colorDeep: '#0f172a', colorMid: '#475569', colorBright: '#cbd5e1',
    voiceMix: 0.20,
    shellColor: '#64748b', shellOpacity: 0.35,
    particleCount: 400, particleColor: '#e2e8f0', particleRadius: 0.95,
    dpr: 0.7,
  },
};

export type GalaxyPaletteName = keyof typeof GALAXY_PRESETS;
