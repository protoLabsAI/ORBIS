import type { FieldSpec } from '../../shared/field-types';

/** Spectrum variant field schema for the customize panel. */
export const SPECTRUM_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Primary',         section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Secondary',       section: 'color' },

  { kind: 'slider', key: 'density',             label: 'Density',         section: 'energy',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'atmosphereGlow',      label: 'Glow',            section: 'energy',  min: 0.0, max: 5.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereLevel',     label: 'Halo thickness',  section: 'energy',  min: 0.1, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereScale',     label: 'Halo scale',      section: 'energy',  min: 1.0, max: 1.1,  step: 0.001 },
  { kind: 'slider', key: 'glow',                label: 'Core glow',       section: 'energy',  min: 0.1, max: 2.5,  step: 0.05  },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',      section: 'energy',  min: 0.0, max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'speed',               label: 'Internal speed',  section: 'motion',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation',   section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'asymmetry',           label: 'Asymmetry',       section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },

  { kind: 'slider', key: 'fractalScale',        label: 'Scale',           section: 'fractal', min: 0.5, max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'fadeOuter',           label: 'Halo outer',      section: 'fractal', min: 1.0, max: 5.0,  step: 0.05  },
  { kind: 'slider', key: 'fadeInner',           label: 'Halo inner',      section: 'fractal', min: 0.5, max: 4.0,  step: 0.05  },
  { kind: 'slider', key: 'smoothing',           label: 'Smoothing',       section: 'fractal', min: 0.1, max: 4.0,  step: 0.05  },
  { kind: 'slider', key: 'colorPhaseR',         label: 'Phase R',         section: 'fractal', min: 0.0, max: 10.0, step: 0.1   },
  { kind: 'slider', key: 'colorPhaseG',         label: 'Phase G',         section: 'fractal', min: 0.0, max: 10.0, step: 0.1   },
  { kind: 'slider', key: 'colorPhaseB',         label: 'Phase B',         section: 'fractal', min: 0.0, max: 10.0, step: 0.1   },
  { kind: 'slider', key: 'colorPhaseA',         label: 'Phase A',         section: 'fractal', min: 0.0, max: 10.0, step: 0.1   },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',      section: 'perf',    min: 0.1, max: 2.0,  step: 0.1   },
];
