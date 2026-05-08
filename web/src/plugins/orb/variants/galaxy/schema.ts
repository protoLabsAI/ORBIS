import type { FieldSpec } from '../../shared/field-types';

/**
 * Galaxy variant field schema. Three-layer composition produces a
 * deep tunables surface — the user can author the plasma, the
 * shell, and the particle field independently.
 */
export const GALAXY_FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Primary tint',   section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Secondary tint', section: 'color' },
  { kind: 'color',  key: 'colorDeep',           label: 'Plasma deep',    section: 'color' },
  { kind: 'color',  key: 'colorMid',            label: 'Plasma mid',     section: 'color' },
  { kind: 'color',  key: 'colorBright',         label: 'Plasma bright',  section: 'color' },
  { kind: 'color',  key: 'shellColor',          label: 'Shell',          section: 'color' },
  { kind: 'color',  key: 'particleColor',       label: 'Particle',       section: 'color' },

  { kind: 'slider', key: 'density',             label: 'Density',        section: 'energy',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'atmosphereGlow',      label: 'Glow',           section: 'energy',  min: 0.0, max: 5.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereLevel',     label: 'Halo thickness', section: 'energy',  min: 0.1, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereScale',     label: 'Halo scale',     section: 'energy',  min: 1.0, max: 1.1,  step: 0.001 },
  { kind: 'slider', key: 'plasmaBrightness',    label: 'Plasma bright',  section: 'energy',  min: 0.5, max: 3.0,  step: 0.05  },
  { kind: 'slider', key: 'voidThreshold',       label: 'Voids',          section: 'energy',  min: 0.0, max: 0.8,  step: 0.01  },
  { kind: 'slider', key: 'shellOpacity',        label: 'Shell opacity',  section: 'energy',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'voiceMix',            label: 'Voice tint',     section: 'energy',  min: 0.0, max: 1.0,  step: 0.05  },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',     section: 'energy',  min: 0.0, max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'speed',               label: 'Internal speed', section: 'motion',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation',  section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'asymmetry',           label: 'Asymmetry',      section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },

  { kind: 'slider', key: 'plasmaScale',         label: 'Plasma scale',   section: 'fractal', min: 0.05, max: 0.5, step: 0.01  },
  { kind: 'slider', key: 'particleCount',       label: 'Particle count', section: 'fractal', min: 100,  max: 1500, step: 50   },
  { kind: 'slider', key: 'particleRadius',      label: 'Particle radius',section: 'fractal', min: 0.5,  max: 1.0,  step: 0.01 },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',     section: 'perf',    min: 0.1, max: 2.0,  step: 0.1   },
];
