/**
 * Disco variant palettes — a faceted mirror ball whose tiles reflect a neon
 * environment built from three colours. `primaryEnergy` + `secondaryEnergy` are
 * the state-crossfaded neon tints; `tertiaryEnergy` is a static accent. Seeded
 * from the lil-gui prototype.
 */

export interface DiscoPreset {
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
  // --- disco-specific ---
  tertiaryEnergy: string; // 3rd neon accent
  facets: number; // facet resolution
  roughness: number; // reflection scatter
  fractalScale: number; // env noise scale
  fractalAmp: number; // env noise amplitude
  bgSpeed: number; // env animation
  coreSpeed: number; // facet-patch animation
  brightness: number;
  bloom: number;
}

export const DISCO_PRESETS: Record<string, DiscoPreset> = {
  // The prototype look — pink / mint / purple neon.
  Disco: {
    primaryEnergy: '#eb3399', secondaryEnergy: '#19ffcc', tertiaryEnergy: '#b266ff',
    density: 1.0, atmosphereGlow: 0.30, speed: 1.0, chromaticAberration: 0.012,
    asymmetry: 0.30, orbRotation: 0.35, dpr: 0.8,
    facets: 15.8, roughness: 0.02, fractalScale: 1.44, fractalAmp: 0.59,
    bgSpeed: 0.10, coreSpeed: 0.05, brightness: 1.2, bloom: 1.0,
  },
  // Silver mirror ball — the classic.
  Mono: {
    primaryEnergy: '#f4f4f5', secondaryEnergy: '#cfd3dc', tertiaryEnergy: '#a8b0c0',
    density: 1.0, atmosphereGlow: 0.26, speed: 0.9, chromaticAberration: 0.008,
    asymmetry: 0.25, orbRotation: 0.32, dpr: 0.8,
    facets: 18.0, roughness: 0.01, fractalScale: 1.5, fractalAmp: 0.5,
    bgSpeed: 0.08, coreSpeed: 0.03, brightness: 1.1, bloom: 0.9,
  },
  // Studio 54 — warm gold / red / amber.
  Studio: {
    primaryEnergy: '#ffd166', secondaryEnergy: '#ef476f', tertiaryEnergy: '#ff7b00',
    density: 1.0, atmosphereGlow: 0.32, speed: 0.95, chromaticAberration: 0.014,
    asymmetry: 0.30, orbRotation: 0.34, dpr: 0.8,
    facets: 15.0, roughness: 0.03, fractalScale: 1.4, fractalAmp: 0.62,
    bgSpeed: 0.12, coreSpeed: 0.05, brightness: 1.25, bloom: 1.0,
  },
  // Cyber — blue / cyan / magenta.
  Cyber: {
    primaryEnergy: '#3b82f6', secondaryEnergy: '#22d3ee', tertiaryEnergy: '#e040fb',
    density: 1.0, atmosphereGlow: 0.34, speed: 1.05, chromaticAberration: 0.018,
    asymmetry: 0.40, orbRotation: 0.36, dpr: 0.8,
    facets: 16.5, roughness: 0.02, fractalScale: 1.5, fractalAmp: 0.58,
    bgSpeed: 0.14, coreSpeed: 0.06, brightness: 1.2, bloom: 1.1,
  },
  // Vegas — red / gold / pink, loud.
  Vegas: {
    primaryEnergy: '#ff2d55', secondaryEnergy: '#ffd60a', tertiaryEnergy: '#ff5db1',
    density: 1.0, atmosphereGlow: 0.36, speed: 1.0, chromaticAberration: 0.016,
    asymmetry: 0.35, orbRotation: 0.35, dpr: 0.8,
    facets: 14.5, roughness: 0.03, fractalScale: 1.42, fractalAmp: 0.6,
    bgSpeed: 0.12, coreSpeed: 0.05, brightness: 1.3, bloom: 1.05,
  },
};

export type DiscoPaletteName = keyof typeof DISCO_PRESETS;
