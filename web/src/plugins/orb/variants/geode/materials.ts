import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import geodeVert from '../../shared/shaders/sphere.vert.glsl';
import geodeFrag from './shaders/geode.frag.glsl';

/**
 * Geode material — a glowing octahedron of volumetric plasma with neon
 * wireframe edges, raymarched inside the orb's proxy sphere. Plasma colour =
 * state primary, frame (edge) colour = state secondary; brightness folds in
 * state glow + bot voice. Seeded from the "octa plasma" prototype.
 */
export const GeodeMaterial = shaderMaterial(
  {
    uTime: 0,
    uLocalCamPos: new THREE.Vector3(),
    uColorPlasma: new THREE.Color('#dfeaff'),
    uColorFrame: new THREE.Color('#eef1ff'),
    uShapeSize: 1.6,
    uShapeStretch: 0.7,
    uPlasmaDensity: 0.05,
    uPlasmaScale: 3.0,
    uBrightness: 1.0,
    uShellInner: 1.9,
    uMaxSteps: 140,
  },
  geodeVert,
  geodeFrag,
);

extend({ GeodeMaterial });

declare module '@react-three/fiber' {
  interface ThreeElements {
    geodeMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
