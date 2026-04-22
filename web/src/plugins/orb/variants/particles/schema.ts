import type { FieldSpec } from '../../shared/field-types';

export const PARTICLES_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Primary',        section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Secondary',      section: 'color' },

  { kind: 'slider', key: 'density',             label: 'Radial push',    section: 'energy',  min: 0.0, max: 1.5,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereGlow',      label: 'Halo glow',      section: 'energy',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereLevel',     label: 'Halo thickness', section: 'energy',  min: 0.1, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereScale',     label: 'Halo scale',     section: 'energy',  min: 1.0, max: 1.1,  step: 0.001 },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',     section: 'energy',  min: 0.0, max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'speed',               label: 'Rotation speed', section: 'motion',  min: 0.1, max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation',  section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'asymmetry',           label: 'Asymmetry',      section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },

  { kind: 'slider', key: 'count',               label: 'Particle count', section: 'fractal', min: 400, max: 3200, step: 100   },
  { kind: 'slider', key: 'particleSize',        label: 'Particle size',  section: 'fractal', min: 0.005, max: 0.05, step: 0.001 },
  { kind: 'slider', key: 'jitter',              label: 'Voice jitter',   section: 'fractal', min: 0.0, max: 0.3,  step: 0.005 },
  { kind: 'slider', key: 'radius',              label: 'Shell radius',   section: 'fractal', min: 0.6, max: 1.8,  step: 0.01  },
  { kind: 'slider', key: 'hueSpread',           label: 'Hue spread',     section: 'fractal', min: 0,   max: 60,   step: 1     },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',     section: 'perf',    min: 0.1, max: 2.0,  step: 0.1   },
];
