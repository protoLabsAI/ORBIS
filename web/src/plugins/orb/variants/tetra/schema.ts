import type { FieldSpec } from '../../shared/field-types';

/**
 * Tetra variant field schema. Consumed by the settings panel to
 * render sliders / colour pickers, by the Randomize/Copy helpers,
 * and by the panel's persistence layer.
 *
 * Sections reuse the variant-agnostic SectionId vocabulary
 * (color/energy/motion/fractal/perf) so the panel groups identically
 * to fractal/nebula and the user doesn't see a different layout per
 * variant.
 */
export const TETRA_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Primary',        section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Secondary',      section: 'color' },

  { kind: 'slider', key: 'density',             label: 'Density',        section: 'energy',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'atmosphereGlow',      label: 'Glow',           section: 'energy',  min: 0.0, max: 5.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereLevel',     label: 'Halo thickness', section: 'energy',  min: 0.1, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereScale',     label: 'Halo scale',     section: 'energy',  min: 1.0, max: 1.1,  step: 0.001 },
  { kind: 'slider', key: 'glowIntensity',       label: 'Volumetric glow',section: 'energy',  min: 0.0, max: 0.10, step: 0.001 },
  { kind: 'slider', key: 'glowBase',            label: 'Glow base',      section: 'energy',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',     section: 'energy',  min: 0.0, max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'speed',               label: 'Internal speed', section: 'motion',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation',  section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'internalAnim',        label: 'Anim speed',     section: 'motion',  min: 0.0, max: 0.5,  step: 0.01  },
  { kind: 'slider', key: 'asymmetry',           label: 'Asymmetry',      section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },

  { kind: 'slider', key: 'shapeSize',           label: 'Shape size',     section: 'fractal', min: 0.5, max: 3.0,  step: 0.05  },
  { kind: 'slider', key: 'iterations',          label: 'Iterations',     section: 'fractal', min: 1,   max: 10,   step: 1     },
  { kind: 'slider', key: 'foldX',               label: 'Fold X',         section: 'fractal', min: 0.0, max: 3.0,  step: 0.05  },
  { kind: 'slider', key: 'foldY',               label: 'Fold Y',         section: 'fractal', min: 0.0, max: 3.0,  step: 0.05  },
  { kind: 'slider', key: 'foldZ',               label: 'Fold Z',         section: 'fractal', min: 0.0, max: 3.0,  step: 0.05  },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',     section: 'perf',    min: 0.1, max: 2.0,  step: 0.1   },
];
