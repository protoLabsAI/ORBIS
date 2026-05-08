/**
 * Liquid variant palettes. Carries OrbBasePreset shape (used by
 * useStateCrossfade) plus the liquid-specific tunables (sphere size,
 * domain-warp parameters, 4-colour procedural palette, Phong lighting
 * setup).
 *
 * The 4-colour palette is what determines the "skin" — keep stops
 * close-in-hue for a single-colour mercury feel; spread them for a
 * rainbow oil-puddle look.
 */

export interface LiquidPreset {
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

  // Surface shape.
  sphereSize: number;       // base orb radius
  heightAmp: number;        // height-field displacement
  warpAmp: number;          // domain-warp amplitude
  warpFalloff: number;
  warpStartFreq: number;
  warpSteps: number;        // 1..20
  warpVelocity: number;     // animates the warp
  noiseContrast: number;

  // 4-stop procedural palette (the "skin" colour).
  liquidColor1: string;
  liquidColor2: string;
  liquidColor3: string;
  liquidColor4: string;

  // Phong lighting.
  ambient: number;
  diffuse: number;
  fillLight: number;
  specularPower: number;    // 1..128 — sharpness of the highlight
  specularIntensity: number;

  // Performance.
  dpr: number;
}

export const LIQUID_PRESETS: Record<string, LiquidPreset> = {
  // Default — deep blue mercury with high-contrast specular.
  Mercury: {
    primaryEnergy: '#7dd3fc', secondaryEnergy: '#1e3a8a',
    density: 1.4, atmosphereGlow: 0.16, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.5, chromaticAberration: 0.014, asymmetry: 0.40, orbRotation: 0.45,
    sphereSize: 1.0, heightAmp: 0.35,
    warpAmp: 0.6, warpFalloff: 1.2, warpStartFreq: 6.0,
    warpSteps: 10.0, warpVelocity: -0.4, noiseContrast: 0.6,
    liquidColor1: '#002aff', liquidColor2: '#0040ff',
    liquidColor3: '#4400ff', liquidColor4: '#330aff',
    ambient: 0.09, diffuse: 0.6, fillLight: 0.2,
    specularPower: 32.0, specularIntensity: 0.5,
    dpr: 0.7,
  },
  // Warm — copper / brass with warmer fill light.
  Copper: {
    primaryEnergy: '#fb923c', secondaryEnergy: '#7c2d12',
    density: 1.6, atmosphereGlow: 0.20, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.55, chromaticAberration: 0.018, asymmetry: 0.50, orbRotation: 0.50,
    sphereSize: 1.0, heightAmp: 0.40,
    warpAmp: 0.7, warpFalloff: 1.2, warpStartFreq: 5.5,
    warpSteps: 11.0, warpVelocity: -0.35, noiseContrast: 0.7,
    liquidColor1: '#7c2d12', liquidColor2: '#c2410c',
    liquidColor3: '#fb923c', liquidColor4: '#fbbf24',
    ambient: 0.10, diffuse: 0.65, fillLight: 0.25,
    specularPower: 24.0, specularIntensity: 0.55,
    dpr: 0.7,
  },
  // Rainbow oil-puddle — wide-spread palette stops give the iridescent feel.
  Oil: {
    primaryEnergy: '#a855f7', secondaryEnergy: '#0ea5e9',
    density: 1.5, atmosphereGlow: 0.18, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.55, chromaticAberration: 0.022, asymmetry: 0.55, orbRotation: 0.50,
    sphereSize: 1.0, heightAmp: 0.30,
    warpAmp: 0.5, warpFalloff: 1.25, warpStartFreq: 7.0,
    warpSteps: 12.0, warpVelocity: -0.45, noiseContrast: 0.55,
    liquidColor1: '#0ea5e9', liquidColor2: '#a855f7',
    liquidColor3: '#ec4899', liquidColor4: '#22d3ee',
    ambient: 0.10, diffuse: 0.7, fillLight: 0.25,
    specularPower: 48.0, specularIntensity: 0.6,
    dpr: 0.7,
  },
  // Quiet — softer warps, low contrast, monochrome silver.
  Silver: {
    primaryEnergy: '#cbd5e1', secondaryEnergy: '#475569',
    density: 1.1, atmosphereGlow: 0.12, atmosphereLevel: 1.0, atmosphereScale: 1.03,
    speed: 0.4, chromaticAberration: 0.010, asymmetry: 0.30, orbRotation: 0.40,
    sphereSize: 1.0, heightAmp: 0.25,
    warpAmp: 0.45, warpFalloff: 1.15, warpStartFreq: 5.0,
    warpSteps: 8.0, warpVelocity: -0.30, noiseContrast: 0.5,
    liquidColor1: '#475569', liquidColor2: '#64748b',
    liquidColor3: '#94a3b8', liquidColor4: '#cbd5e1',
    ambient: 0.08, diffuse: 0.55, fillLight: 0.20,
    specularPower: 64.0, specularIntensity: 0.45,
    dpr: 0.7,
  },
};

export type LiquidPaletteName = keyof typeof LIQUID_PRESETS;
