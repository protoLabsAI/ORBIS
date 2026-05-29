import type { FieldSpec } from '../../shared/field-types';

/**
 * Lattice variant field schema. Section names match the variant-
 * agnostic SectionId vocabulary (color/energy/motion/fractal/perf)
 * so the panel groups identically to the other variants and the user
 * doesn't see a different layout per variant.
 */
export const LATTICE_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Primary',         section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Secondary',       section: 'color' },

  { kind: 'slider', key: 'density',             label: 'Density',         section: 'energy',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'atmosphereGlow',      label: 'Glow',            section: 'energy',  min: 0.0, max: 5.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereLevel',     label: 'Halo thickness',  section: 'energy',  min: 0.1, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereScale',     label: 'Halo scale',      section: 'energy',  min: 1.0, max: 1.1,  step: 0.001 },
  { kind: 'slider', key: 'glow',                label: 'Core glow',       section: 'energy',  min: 0.0, max: 1.5,  step: 0.01  },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',      section: 'energy',  min: 0.0, max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'speed',               label: 'Internal speed',  section: 'motion',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation',   section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'asymmetry',           label: 'Asymmetry',       section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },

  { kind: 'slider', key: 'cubeSize',            label: 'Cube size',       section: 'fractal', min: 0.5, max: 4.0,  step: 0.1   },
  { kind: 'slider', key: 'gridScale',           label: 'Grid scale',      section: 'fractal', min: 0.3, max: 3.0,  step: 0.05  },
  { kind: 'slider', key: 'distortion',          label: 'Distortion',      section: 'fractal', min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'colorOffsetR',        label: 'Color shift R',   section: 'fractal', min: 0.0, max: 5.0,  step: 0.1   },
  { kind: 'slider', key: 'colorOffsetG',        label: 'Color shift G',   section: 'fractal', min: 0.0, max: 5.0,  step: 0.1   },
  { kind: 'slider', key: 'colorOffsetB',        label: 'Color shift B',   section: 'fractal', min: 0.0, max: 5.0,  step: 0.1   },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',      section: 'perf',    min: 0.1, max: 2.0,  step: 0.1   },
];
