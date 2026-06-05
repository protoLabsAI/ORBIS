import type { FieldSpec } from '../../shared/field-types';

/** Edison variant field schema — consumed by the customization panel. */
export const EDISON_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Filament',        section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Glow',            section: 'color' },

  { kind: 'slider', key: 'bloom',               label: 'Bloom',           section: 'energy',  min: 0.0,  max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'atmosphereGlow',      label: 'Glow strength',   section: 'energy',  min: 0.0,  max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'capsuleOpacity',      label: 'Glass',           section: 'energy',  min: 0.0,  max: 0.2,  step: 0.005 },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',      section: 'energy',  min: 0.0,  max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'waveSpeed',           label: 'Writhe speed',    section: 'motion',  min: 0.0,  max: 3.0,  step: 0.05  },
  { kind: 'slider', key: 'particleSpeed',       label: 'Flow',            section: 'motion',  min: 0.0,  max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation',   section: 'motion',  min: 0.0,  max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'speed',               label: 'Speed',           section: 'motion',  min: 0.1,  max: 2.0,  step: 0.05  },

  { kind: 'slider', key: 'curlFrequency',       label: 'Coil',            section: 'fractal', min: 0.05, max: 0.5,  step: 0.01  },
  { kind: 'slider', key: 'writhingAmplitude',   label: 'Sway',            section: 'fractal', min: 0.0,  max: 3.0,  step: 0.05  },
  { kind: 'slider', key: 'tentacleCount',       label: 'Filaments',       section: 'fractal', min: 3,    max: 14,   step: 1     },
  { kind: 'slider', key: 'filaments',           label: 'Strands',         section: 'fractal', min: 4,    max: 24,   step: 1     },
  { kind: 'slider', key: 'particleSpread',      label: 'Particle spread', section: 'fractal', min: 0.0,  max: 1.0,  step: 0.02  },

  { kind: 'slider', key: 'particleCount',       label: 'Particles',       section: 'perf',    min: 200,  max: 3000, step: 100   },
  { kind: 'slider', key: 'particleSize',        label: 'Particle size',   section: 'perf',    min: 0.01, max: 0.1,  step: 0.005 },
  { kind: 'slider', key: 'dpr',                 label: 'Resolution',      section: 'perf',    min: 0.3,  max: 1.5,  step: 0.1   },
];
