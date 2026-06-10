import * as THREE from 'three';
import type { OrbDefinition, UniformDecl } from '../definition/types';

export type UniformRecord = Record<string, THREE.IUniform>;

function defaultValue(decl: UniformDecl): THREE.IUniform['value'] {
  switch (decl.type) {
    case 'float':
      return typeof decl.default === 'number' ? decl.default : 0;
    case 'color':
      return new THREE.Color(typeof decl.default === 'string' ? decl.default : '#ffffff');
    case 'vec2': {
      const d = Array.isArray(decl.default) ? decl.default : [0, 0];
      return new THREE.Vector2(d[0] ?? 0, d[1] ?? 0);
    }
    case 'vec3': {
      const d = Array.isArray(decl.default) ? decl.default : [0, 0, 0];
      return new THREE.Vector3(d[0] ?? 0, d[1] ?? 0, d[2] ?? 0);
    }
    case 'vec4': {
      const d = Array.isArray(decl.default) ? decl.default : [0, 0, 0, 0];
      return new THREE.Vector4(d[0] ?? 0, d[1] ?? 0, d[2] ?? 0, d[3] ?? 0);
    }
  }
}

/** Standard uniforms (engine-managed) + the definition's declared ones. */
export function buildUniforms(def: OrbDefinition): UniformRecord {
  const uniforms: UniformRecord = {
    uTime: { value: 0 },
    uLocalCamPos: { value: new THREE.Vector3() },
    uPrimaryColor: { value: new THREE.Color('#9b87f2') },
    uSecondaryColor: { value: new THREE.Color('#6366f1') },
    uClickDir: { value: new THREE.Vector3(0, 0, 1) },
    uClickStrength: { value: 0 },
  };
  for (const [name, decl] of Object.entries(def.uniforms)) {
    if (name in uniforms) continue; // validator rejects shadowing; belt-and-braces
    uniforms[name] = { value: defaultValue(decl) };
  }
  return uniforms;
}

/** Fresh copy of a uniform's declared default — the per-frame binding
 * accumulator starting point for scalar (float / component) targets. */
export function scalarDefault(def: OrbDefinition, uniformName: string): number {
  const decl = def.uniforms[uniformName];
  if (decl && decl.type === 'float' && typeof decl.default === 'number') return decl.default;
  if (uniformName === 'uClickStrength') return 0;
  return 0;
}
