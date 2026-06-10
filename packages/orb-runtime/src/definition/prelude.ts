import type { OrbDefinition, UniformType } from './types';

/**
 * The standard GLSL contract every raymarch-v1 definition gets for free.
 * Matches the convention of the hand-written raymarched variants:
 * ray origin = uLocalCamPos, ray dir = normalize(vLocalPosition -
 * uLocalCamPos), R3F mesh rotation handles auto-rotate + drag.
 */
export const STANDARD_UNIFORM_NAMES = [
  'uTime',
  'uLocalCamPos',
  'uPrimaryColor',
  'uSecondaryColor',
  'uClickDir',
  'uClickStrength',
] as const;

/** Engine-managed targets a definition may not bind (set every frame by the engine). */
export const RESERVED_BINDING_TARGETS = new Set(['uTime', 'uLocalCamPos', 'uClickDir']);

const STANDARD_PRELUDE = `// — orbis standard prelude (injected) —
uniform float uTime;
uniform vec3  uLocalCamPos;
uniform vec3  uPrimaryColor;
uniform vec3  uSecondaryColor;
uniform vec3  uClickDir;
uniform float uClickStrength;
varying vec3 vLocalPosition;
varying vec3 vNormal;
varying vec3 vViewPosition;
`;

/** Shared sphere vertex shader — identical to web/src/plugins/orb/shared/shaders/sphere.vert.glsl. */
export const SPHERE_VERTEX_SHADER = `varying vec3 vLocalPosition;
varying vec3 vNormal;
varying vec3 vViewPosition;

void main() {
  vLocalPosition = position;
  vNormal = normalize(normalMatrix * normal);
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  vViewPosition = -mvPosition.xyz;
  gl_Position = projectionMatrix * mvPosition;
}
`;

const GLSL_TYPE: Record<UniformType, string> = {
  float: 'float',
  vec2: 'vec2',
  vec3: 'vec3',
  vec4: 'vec4',
  color: 'vec3',
};

/** Build the full fragment source: standard prelude + declared uniforms + body. */
export function buildFragmentSource(def: OrbDefinition): string {
  const standard = new Set<string>(STANDARD_UNIFORM_NAMES);
  const declared = Object.entries(def.uniforms)
    .filter(([name]) => !standard.has(name))
    .map(([name, decl]) => `uniform ${GLSL_TYPE[decl.type]} ${name};`)
    .join('\n');
  return `${STANDARD_PRELUDE}${declared ? declared + '\n' : ''}// — definition body —\n${def.shaders.fragment}`;
}

export function vertexSource(def: OrbDefinition): string {
  return def.shaders.vertex && def.shaders.vertex.trim().length > 0
    ? def.shaders.vertex
    : SPHERE_VERTEX_SHADER;
}
