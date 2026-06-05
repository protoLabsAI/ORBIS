import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import discoVert from '../../shared/shaders/sphere.vert.glsl';
import discoFrag from './shaders/disco.frag.glsl';

/**
 * Disco core material — a faceted mirror sphere reflecting a neon environment.
 * Opaque (unlike the additive volumetric variants): the default ShaderMaterial
 * writes depth and uses normal blending, so it renders a solid ball.
 */
export const DiscoMaterial = shaderMaterial(
  {
    uTime: 0,
    uLocalCamPos: new THREE.Vector3(),
    uColor1: new THREE.Color('#eb3399'),
    uColor2: new THREE.Color('#19ffcc'),
    uColor3: new THREE.Color('#b266ff'),
    uFacets: 15.8,
    uRoughness: 0.02,
    uFractalScale: 1.44,
    uFractalAmp: 0.59,
    uBgSpeed: 0.1,
    uCoreSpeed: 0.05,
    uBrightness: 1.2,
  },
  discoVert,
  discoFrag,
);

extend({ DiscoMaterial });

declare module '@react-three/fiber' {
  interface ThreeElements {
    discoMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
