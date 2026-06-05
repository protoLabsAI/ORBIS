import type { FieldSpec } from '../../shared/field-types';

/** Geode variant field schema — consumed by the customization panel. */
export const GEODE_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Plasma',        section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Frame',         section: 'color' },

  { kind: 'slider', key: 'bloom',               label: 'Bloom',         section: 'energy',  min: 0.0,  max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'brightness',          label: 'Brightness',    section: 'energy',  min: 0.1,  max: 3.0,  step: 0.01  },
  { kind: 'slider', key: 'plasmaDensity',       label: 'Plasma density',section: 'energy',  min: 0.01, max: 0.12, step: 0.001 },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',    section: 'energy',  min: 0.0,  max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation', section: 'motion',  min: 0.0,  max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'speed',               label: 'Speed',         section: 'motion',  min: 0.1,  max: 2.0,  step: 0.05  },

  { kind: 'slider', key: 'shapeSize',           label: 'Gem size',      section: 'fractal', min: 1.0,  max: 1.85, step: 0.01  },
  { kind: 'slider', key: 'shapeStretch',        label: 'Vertical cut',  section: 'fractal', min: 0.4,  max: 1.3,  step: 0.01  },
  { kind: 'slider', key: 'plasmaScale',         label: 'Plasma scale',  section: 'fractal', min: 1.0,  max: 6.0,  step: 0.1   },

  { kind: 'slider', key: 'maxSteps',            label: 'Raymarch detail', section: 'perf',  min: 60,   max: 200,  step: 2     },
  { kind: 'slider', key: 'dpr',                 label: 'Resolution',    section: 'perf',    min: 0.3,  max: 1.5,  step: 0.1   },
];
