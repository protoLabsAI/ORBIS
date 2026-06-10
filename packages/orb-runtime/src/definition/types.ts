/**
 * The `.orbis` orb definition format — v1.
 *
 * A definition is pure data: GLSL fragment body + typed uniform
 * declarations + settings fields + palettes + declarative
 * signal→uniform bindings. No executable JS anywhere in the format —
 * that is the security model for runtime-imported orbs.
 *
 * Plan of record: docs/internal/orb-format-and-editor.md.
 */

import type { FieldSpec, MoodOverrides } from '../types';

export const ORBIS_FORMAT = 'orbis-orb' as const;
export const ORBIS_VERSION = 1 as const;

/** `color` is a vec3 in GLSL; declared separately so defaults can be hex strings. */
export type UniformType = 'float' | 'vec2' | 'vec3' | 'vec4' | 'color';

export interface UniformDecl {
  type: UniformType;
  /** float → number; vec2/3/4 → number[]; color → "#rrggbb". */
  default?: number | number[] | string;
}

export type BindingOp = 'set' | 'add' | 'mul';
export type BindingCurve = 'linear' | 'exp' | 'smoothstep';

/**
 * One signal→uniform wire, evaluated per frame in declaration order.
 * For each target, the accumulator starts at the uniform's declared
 * default each frame; then `value = curve(signal) * scale + offset`
 * and `acc = acc <op> value`.
 *
 * Scalar signals:
 *   time | bot.level | user.level | breath | pointer.clickStrength |
 *   mood.valence | mood.arousal | mood.guardedness |
 *   snap.density | snap.glow | snap.speed | snap.ca | snap.asymmetry |
 *   snap.rotation | snap.scale | param.<key>
 * Color signals (op must be 'set'):
 *   snap.primary | snap.secondary | param.<key> (string param)
 *
 * Targets are uniform names, optionally with a component suffix for
 * vector uniforms: "uColorPhases.x" (x|y|z|w).
 */
export interface Binding {
  target: string;
  signal: string;
  op?: BindingOp;        // default 'set'
  scale?: number;        // default 1
  offset?: number;       // default 0
  curve?: BindingCurve;  // default 'linear'
  /** Optional one-pole smoothing factor per frame, (0, 1]. Lower = smoother. */
  smooth?: number;
}

export interface GeometryOpts {
  type?: 'sphere';        // raymarch-v1 renders on a sphere shell
  radius?: number;        // default 5.5 (matches the raymarch family)
  widthSegments?: number; // default 32
  heightSegments?: number;// default 32
}

export interface MaterialOpts {
  blending?: 'additive' | 'normal'; // default 'additive'
  transparent?: boolean;            // default true
  depthWrite?: boolean;             // default false
  doubleSide?: boolean;             // default true
}

export interface MotionOpts {
  /** Mesh-scale pump from bot speech level: scale *= 1 + bot.level * pump. Default 0.06. */
  scaleAudioPump?: number;
  /** Idle-breath scale modulation on/off. Default true. */
  breath?: boolean;
  /** rotation.x speed as a ratio of rotation.y speed. Default 0.5. */
  rotationXRatio?: number;
}

export interface BloomOpts {
  intensity?: number;
  luminanceThreshold?: number;
  luminanceSmoothing?: number;
  mipmapBlur?: boolean;
}

export interface OrbDefinition {
  format: typeof ORBIS_FORMAT;
  version: typeof ORBIS_VERSION;
  /** Slug id — [a-z0-9-], unique within the registry. */
  id: string;
  name: string;
  author?: string;
  description?: string;

  /** Which runtime renders this definition. */
  engine: 'raymarch-v1';
  geometry?: GeometryOpts;
  material?: MaterialOpts;
  motion?: MotionOpts;

  shaders: {
    /** GLSL fragment BODY — the standard prelude (uniform/varying decls) is injected. */
    fragment: string;
    /** Optional vertex override; null/absent → the shared sphere vertex shader. */
    vertex?: string | null;
  };

  /** Author-declared uniforms. Standard uniforms are implicit (see prelude.ts). */
  uniforms: Record<string, UniformDecl>;

  fields: FieldSpec[];
  palettes: Record<string, Record<string, number | string>>;
  defaultPalette: string;

  bindings: Binding[];

  moodDefaults?: MoodOverrides | null;
  premium?: boolean;
  post?: { bloom?: BloomOpts } | null;
}
