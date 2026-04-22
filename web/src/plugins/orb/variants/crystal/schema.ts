import type { FieldSpec } from '../../shared/field-types';

/**
 * Crystal variant field schema. Shared base fields (color/energy/motion/perf)
 * keep their stateSnapshot semantics — `density` is reused as a "wobble"
 * amplitude knob because every variant uses the same shared state snapshot.
 * Crystal-specific optical knobs go under the 'fractal' section (cosmetic
 * label says "Fractal" — will be fixed when sections become variant-owned).
 */
export const CRYSTAL_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Primary',        section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Secondary',      section: 'color' },

  { kind: 'slider', key: 'density',             label: 'Wobble',         section: 'energy',  min: 0.0, max: 1.5,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereGlow',      label: 'Halo glow',      section: 'energy',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereLevel',     label: 'Halo thickness', section: 'energy',  min: 0.1, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereScale',     label: 'Halo scale',     section: 'energy',  min: 1.0, max: 1.1,  step: 0.001 },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',     section: 'energy',  min: 0.0, max: 0.3,  step: 0.001 },

  { kind: 'slider', key: 'speed',               label: 'Rotation speed', section: 'motion',  min: 0.1, max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation',  section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'asymmetry',           label: 'Asymmetry',      section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },

  { kind: 'slider', key: 'transmission',        label: 'Transmission',   section: 'fractal', min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'ior',                 label: 'IOR',            section: 'fractal', min: 1.0, max: 2.5,  step: 0.01  },
  { kind: 'slider', key: 'thickness',           label: 'Thickness',      section: 'fractal', min: 0.0, max: 2.0,  step: 0.01  },
  { kind: 'slider', key: 'roughness',           label: 'Roughness',      section: 'fractal', min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'iridescence',         label: 'Iridescence',    section: 'fractal', min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'envIntensity',        label: 'Env reflection', section: 'fractal', min: 0.0, max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'detail',              label: 'Facets (0=low)', section: 'fractal', min: 0,   max: 3,    step: 1     },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',     section: 'perf',    min: 0.1, max: 2.0,  step: 0.1   },
];
