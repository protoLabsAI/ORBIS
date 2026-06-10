/**
 * @orbis/orb-runtime — public surface.
 *
 * Consumed as source via bundler alias by web/ (the app) and
 * sites/editor/ (the orb editor). See README.md and
 * docs/internal/orb-format-and-editor.md.
 */

// Core types
export type {
  VoiceState,
  Mood,
  ParamMap,
  StateOverrides,
  MoodOverrides,
  SectionId,
  ColorField,
  SliderField,
  FieldSpec,
} from './types';

// Pure math / tuning core
export * from './constants';
export { lerp, clamp01, easeInOutCubic } from './math';
export { withHSL } from './color';
export { Envelope } from './envelope';
export { composeBase } from './compose';
export {
  stateSnapshot,
  lerpSnapshot,
  coerceBasePreset,
  type StateSnapshot,
  type OrbBasePreset,
} from './stateSnapshot';

// Hooks (R3F)
export { useStateCrossfade } from './hooks/useStateCrossfade';
export { useIdleBreath } from './hooks/useIdleBreath';
export { usePointerInteraction, type PointerInteraction } from './hooks/usePointerInteraction';

// .orbis definition format
export {
  ORBIS_FORMAT,
  ORBIS_VERSION,
  type OrbDefinition,
  type UniformDecl,
  type UniformType,
  type Binding,
  type BindingOp,
  type BindingCurve,
  type GeometryOpts,
  type MaterialOpts,
  type MotionOpts,
  type BloomOpts,
} from './definition/types';
export {
  validateOrbDefinition,
  type ValidationResult,
  MAX_FRAGMENT_CHARS,
  MAX_DEFINITION_CHARS,
} from './definition/validate';
export {
  buildFragmentSource,
  vertexSource,
  SPHERE_VERTEX_SHADER,
  STANDARD_UNIFORM_NAMES,
  RESERVED_BINDING_TARGETS,
} from './definition/prelude';
export { compileCheck, type CompileCheckResult } from './definition/compile';

// Engine
export { DefinitionOrb, type DefinitionOrbProps } from './engine/DefinitionOrb';
export { buildUniforms, type UniformRecord } from './engine/uniforms';
export { BindingRuntime } from './engine/bindings';
export { resolveScalar, resolveColor, type SignalContext } from './engine/signals';
