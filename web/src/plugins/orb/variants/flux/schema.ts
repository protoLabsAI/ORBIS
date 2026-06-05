import type { FieldSpec } from '../../shared/field-types';

/** Flux variant field schema — consumed by the customization panel. */
export const FLUX_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Colour A',      section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Colour B',      section: 'color' },
  { kind: 'color',  key: 'tertiaryEnergy',      label: 'Colour C',      section: 'color' },

  { kind: 'slider', key: 'bloom',               label: 'Bloom',         section: 'energy',  min: 0.0,  max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'brightness',          label: 'Brightness',    section: 'energy',  min: 0.1,  max: 2.0,  step: 0.01  },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',    section: 'energy',  min: 0.0,  max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'cycleSpeed',          label: 'Colour cycle',  section: 'motion',  min: 0.0,  max: 0.6,  step: 0.01  },
  { kind: 'slider', key: 'pulseIntensity',      label: 'Pulse',         section: 'motion',  min: 0.0,  max: 6.0,  step: 0.1   },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation', section: 'motion',  min: 0.0,  max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'speed',               label: 'Speed',         section: 'motion',  min: 0.1,  max: 2.0,  step: 0.05  },

  { kind: 'slider', key: 'iterations',          label: 'Complexity',    section: 'fractal', min: 1,    max: 30,   step: 1     },
  { kind: 'slider', key: 'distortion',          label: 'Fluid',         section: 'fractal', min: 0.5,  max: 5.0,  step: 0.1   },
  { kind: 'slider', key: 'fractalScale',        label: 'Scale',         section: 'fractal', min: 0.05, max: 0.4,  step: 0.005 },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',    section: 'perf',    min: 0.3,  max: 1.5,  step: 0.1   },
];
