import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import vert from './atmosphere.vert.glsl';
import frag from './atmosphere.frag.glsl';

/**
 * Atmosphere shell material. Additive-blended halo around an orb's
 * silhouette. Shared across all variants that want a glow.
 */
export const AtmosphereMaterial = shaderMaterial(
  {
    uColor: new THREE.Color('#0ea5e9'),
    uColorSecondary: new THREE.Color('#f472b6'),
    uGlow: 0.18,
    uLevel: 1.0,
    uClickDir: new THREE.Vector3(0, 0, 1),
    uClickStrength: 0,
  },
  vert,
  frag,
);

extend({ AtmosphereMaterial });

declare module '@react-three/fiber' {
  interface ThreeElements {
    atmosphereMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
