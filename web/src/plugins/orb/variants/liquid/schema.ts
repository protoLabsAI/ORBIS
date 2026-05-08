import type { FieldSpec } from '../../shared/field-types';

/** Liquid variant field schema. The "skin" of the orb — 4 procedural
 * palette stops + domain-warp params + Phong lighting — lives here.
 *
 * The OrbBasePreset color pickers (primaryEnergy / secondaryEnergy)
 * are kept for the voice-state crossfade tint; the four liquidColorN
 * pickers are the actual surface palette. Two layers of colour
 * authoring is verbose but necessary — voice tint is a separate
 * dimension from the variant's identity colour. */
export const LIQUID_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Primary tint',    section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Secondary tint',  section: 'color' },
  { kind: 'color',  key: 'liquidColor1',        label: 'Skin 1',          section: 'color' },
  { kind: 'color',  key: 'liquidColor2',        label: 'Skin 2',          section: 'color' },
  { kind: 'color',  key: 'liquidColor3',        label: 'Skin 3',          section: 'color' },
  { kind: 'color',  key: 'liquidColor4',        label: 'Skin 4',          section: 'color' },

  { kind: 'slider', key: 'density',             label: 'Density',         section: 'energy',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'atmosphereGlow',      label: 'Glow',            section: 'energy',  min: 0.0, max: 5.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereLevel',     label: 'Halo thickness',  section: 'energy',  min: 0.1, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereScale',     label: 'Halo scale',      section: 'energy',  min: 1.0, max: 1.1,  step: 0.001 },
  { kind: 'slider', key: 'ambient',             label: 'Ambient',         section: 'energy',  min: 0.0, max: 0.3,  step: 0.01  },
  { kind: 'slider', key: 'diffuse',             label: 'Diffuse',         section: 'energy',  min: 0.0, max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'fillLight',           label: 'Fill light',      section: 'energy',  min: 0.0, max: 1.0,  step: 0.05  },
  { kind: 'slider', key: 'specularPower',       label: 'Specular power',  section: 'energy',  min: 1.0, max: 128.0,step: 1.0   },
  { kind: 'slider', key: 'specularIntensity',   label: 'Specular int',    section: 'energy',  min: 0.0, max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',      section: 'energy',  min: 0.0, max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'speed',               label: 'Internal speed',  section: 'motion',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation',   section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'asymmetry',           label: 'Asymmetry',       section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'warpVelocity',        label: 'Surface velocity',section: 'motion',  min: -1.5, max: 1.5, step: 0.05  },

  { kind: 'slider', key: 'sphereSize',          label: 'Sphere size',     section: 'fractal', min: 0.5, max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'heightAmp',           label: 'Surface height',  section: 'fractal', min: 0.0, max: 0.8,  step: 0.01  },
  { kind: 'slider', key: 'warpAmp',             label: 'Warp amp',        section: 'fractal', min: 0.0, max: 1.5,  step: 0.05  },
  { kind: 'slider', key: 'warpFalloff',         label: 'Warp falloff',    section: 'fractal', min: 0.5, max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'warpStartFreq',       label: 'Warp freq',       section: 'fractal', min: 1.0, max: 10.0, step: 0.1   },
  { kind: 'slider', key: 'warpSteps',           label: 'Warp steps',      section: 'fractal', min: 1,   max: 20,   step: 1     },
  { kind: 'slider', key: 'noiseContrast',       label: 'Noise contrast',  section: 'fractal', min: 0.1, max: 2.0,  step: 0.05  },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',      section: 'perf',    min: 0.1, max: 2.0,  step: 0.1   },
];
