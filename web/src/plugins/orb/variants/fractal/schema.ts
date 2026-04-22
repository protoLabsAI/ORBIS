import type { FieldSpec } from '../../shared/field-types';

/**
 * Fractal variant field schema. Consumed by the settings panel to
 * render sliders / color pickers; by the Randomize/Copy helpers to
 * walk the tunable surface; and by the panel's persistence layer.
 */
export const FRACTAL_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Primary',        section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Secondary',      section: 'color' },

  { kind: 'slider', key: 'density',             label: 'Density',        section: 'energy',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'atmosphereGlow',      label: 'Glow',           section: 'energy',  min: 0.0, max: 5.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereLevel',     label: 'Halo thickness', section: 'energy',  min: 0.1, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereScale',     label: 'Halo scale',     section: 'energy',  min: 1.0, max: 1.1,  step: 0.001 },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',     section: 'energy',  min: 0.0, max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'speed',               label: 'Internal speed', section: 'motion',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation',  section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'internalAnim',        label: 'Anim speed',     section: 'motion',  min: 0.0, max: 2.0,  step: 0.01  },

  { kind: 'slider', key: 'fractalIters',        label: 'Iterations',     section: 'fractal', min: 2,    max: 12,   step: 1     },
  { kind: 'slider', key: 'fractalScale',        label: 'Scale',          section: 'fractal', min: 0.3,  max: 1.5,  step: 0.01  },
  { kind: 'slider', key: 'fractalDecay',        label: 'Decay',          section: 'fractal', min: -25,  max: -5,   step: 0.1   },
  { kind: 'slider', key: 'smoothness',          label: 'Smoothness',     section: 'fractal', min: 0.0,  max: 0.15, step: 0.001 },
  { kind: 'slider', key: 'asymmetry',           label: 'Asymmetry',      section: 'fractal', min: 0.0,  max: 1.0,  step: 0.01  },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',     section: 'perf',    min: 0.1,  max: 2.0,  step: 0.1   },
];
