import type { FieldSpec } from '../../shared/field-types';

export const NEBULA_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Primary',        section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Secondary',      section: 'color' },

  { kind: 'slider', key: 'density',             label: 'Density',        section: 'energy',  min: 0.2, max: 3.0,  step: 0.05  },
  { kind: 'slider', key: 'atmosphereGlow',      label: 'Halo glow',      section: 'energy',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereLevel',     label: 'Halo thickness', section: 'energy',  min: 0.1, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereScale',     label: 'Halo scale',     section: 'energy',  min: 1.0, max: 1.1,  step: 0.001 },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',     section: 'energy',  min: 0.0, max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'speed',               label: 'Drift speed',    section: 'motion',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation',  section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'asymmetry',           label: 'Asymmetry',      section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },

  { kind: 'slider', key: 'cloudScale',          label: 'Cloud scale',    section: 'fractal', min: 0.8, max: 4.0,  step: 0.05  },
  { kind: 'slider', key: 'cloudiness',          label: 'Cloudiness',     section: 'fractal', min: 0.3, max: 1.5,  step: 0.01  },
  { kind: 'slider', key: 'drift',               label: 'Drift',          section: 'fractal', min: 0.0, max: 1.5,  step: 0.01  },
  { kind: 'slider', key: 'softness',            label: 'Edge softness',  section: 'fractal', min: 0.1, max: 1.0,  step: 0.01  },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',     section: 'perf',    min: 0.1, max: 2.0,  step: 0.1   },
];
