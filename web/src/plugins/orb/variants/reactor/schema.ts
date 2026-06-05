import type { FieldSpec } from '../../shared/field-types';

/** Reactor variant field schema — consumed by the customization panel. */
export const REACTOR_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Core',          section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Rim',           section: 'color' },

  { kind: 'slider', key: 'bloom',               label: 'Bloom',         section: 'energy',  min: 0.0,  max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'brightness',          label: 'Brightness',    section: 'energy',  min: 0.1,  max: 3.0,  step: 0.01  },
  { kind: 'slider', key: 'colorBoost',          label: 'Glow intensity',section: 'energy',  min: 0.5,  max: 6.0,  step: 0.1   },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',    section: 'energy',  min: 0.0,  max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'lightSpeed',          label: 'Pulse speed',   section: 'motion',  min: 0.0,  max: 12.0, step: 0.1   },
  { kind: 'slider', key: 'autoRotationSpeed',   label: 'Morph',         section: 'motion',  min: 0.0,  max: 3.0,  step: 0.05  },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation', section: 'motion',  min: 0.0,  max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'speed',               label: 'Speed',         section: 'motion',  min: 0.1,  max: 2.0,  step: 0.05  },

  { kind: 'slider', key: 'foldSteps',           label: 'Detail',        section: 'fractal', min: 1,    max: 12,   step: 1     },
  { kind: 'slider', key: 'kifsScale',           label: 'Fold scale',    section: 'fractal', min: 1.0,  max: 1.6,  step: 0.01  },
  { kind: 'slider', key: 'kifsOffset',          label: 'Fold offset',   section: 'fractal', min: 4.0,  max: 14.0, step: 0.1   },
  { kind: 'slider', key: 'textureScale',        label: 'Pattern scale', section: 'fractal', min: 0.5,  max: 3.0,  step: 0.05  },
  { kind: 'slider', key: 'shellInner',          label: 'Shell',         section: 'fractal', min: 1.5,  max: 1.95, step: 0.01  },

  { kind: 'slider', key: 'maxSteps',            label: 'Raymarch detail', section: 'perf',  min: 40,   max: 200,  step: 2     },
  { kind: 'slider', key: 'dpr',                 label: 'Resolution',    section: 'perf',    min: 0.3,  max: 1.5,  step: 0.1   },
];
