import type { FieldSpec } from '../../shared/field-types';

/** Disco variant field schema — consumed by the customization panel. */
export const DISCO_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Neon 1',       section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Neon 2',       section: 'color' },
  { kind: 'color',  key: 'tertiaryEnergy',      label: 'Neon 3',       section: 'color' },

  { kind: 'slider', key: 'bloom',               label: 'Bloom',        section: 'energy',  min: 0.0,  max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'brightness',          label: 'Brightness',   section: 'energy',  min: 0.1,  max: 3.0,  step: 0.05  },
  { kind: 'slider', key: 'roughness',           label: 'Roughness',    section: 'energy',  min: 0.0,  max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',   section: 'energy',  min: 0.0,  max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'orbRotation',         label: 'Spin',         section: 'motion',  min: 0.0,  max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'speed',               label: 'Speed',        section: 'motion',  min: 0.1,  max: 2.0,  step: 0.05  },
  { kind: 'slider', key: 'bgSpeed',             label: 'Light drift',  section: 'motion',  min: 0.0,  max: 1.0,  step: 0.02  },
  { kind: 'slider', key: 'coreSpeed',           label: 'Tile shimmer', section: 'motion',  min: 0.0,  max: 1.0,  step: 0.02  },

  { kind: 'slider', key: 'facets',              label: 'Facets',       section: 'fractal', min: 6.0,  max: 30.0, step: 0.5   },
  { kind: 'slider', key: 'fractalScale',        label: 'Light scale',  section: 'fractal', min: 0.3,  max: 4.0,  step: 0.05  },
  { kind: 'slider', key: 'fractalAmp',          label: 'Light amount', section: 'fractal', min: 0.1,  max: 1.5,  step: 0.02  },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',   section: 'perf',    min: 0.3,  max: 1.5,  step: 0.1   },
];
