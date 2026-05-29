import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import latticeVert from '../../shared/shaders/sphere.vert.glsl';
import latticeFrag from './shaders/lattice.frag.glsl';

/**
 * Lattice core material. AABB-bounded volumetric raymarch through a
 * wrapped-grid SDF — repeating cell-wall shells inside a cube.
 * Distinct from the other volumetric variants (fractal, nebula, tetra)
 * by the cube silhouette + depth-tinted palette.
 */
export const LatticeMaterial = shaderMaterial(
  {
    uTime: 0,
    uLocalCamPos: new THREE.Vector3(),
    uPrimaryColor: new THREE.Color('#7dd3fc'),
    uSecondaryColor: new THREE.Color('#fb7185'),
    uCubeSize: 2.0,
    uGridScale: 1.0,
    uDistortion: 0.0,
    uGlow: 0.6,
    uColorOffset: new THREE.Vector3(2.0, 1.0, 0.0),
    uClickDir: new THREE.Vector3(0, 0, 1),
    uClickStrength: 0,
  },
  latticeVert,
  latticeFrag,
);

extend({ LatticeMaterial });

declare module '@react-three/fiber' {
  interface ThreeElements {
    latticeMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
