import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import tetraVert from '../../shared/shaders/sphere.vert.glsl';
import tetraFrag from './shaders/tetra.frag.glsl';

/**
 * Tetra core material. Sphere-mounted raymarch through a
 * tetrahedron-bounded spherical-inversion fractal; uniforms drive
 * iterations, fold offsets, glow intensity / base, and the
 * primary/secondary palette through the volumetric accumulator.
 */
export const TetraMaterial = shaderMaterial(
  {
    uTime: 0,
    uLocalCamPos: new THREE.Vector3(),
    uPrimaryColor: new THREE.Color('#7dd3fc'),
    uSecondaryColor: new THREE.Color('#fb7185'),
    uShapeSize: 1.6,
    uIterations: 5,
    uFold: new THREE.Vector3(1.7, 0.5, 0.7),
    uGlowIntensity: 0.028,
    uGlowBase: 0.282,
    uInternalAnim: 0.15,
    uClickDir: new THREE.Vector3(0, 0, 1),
    uClickStrength: 0,
  },
  tetraVert,
  tetraFrag,
);

extend({ TetraMaterial });

declare module '@react-three/fiber' {
  interface ThreeElements {
    tetraMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
