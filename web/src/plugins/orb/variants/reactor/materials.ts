import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import reactorVert from '../../shared/shaders/sphere.vert.glsl';
import reactorFrag from './shaders/reactor.frag.glsl';

/**
 * Reactor core material — a KIFS fractal raymarched inside the orb's glass shell.
 * Uniforms drive the fold geometry, glow, and morph; colour comes from the
 * state snapshot (primary × colorBoost) per frame.
 */
export const ReactorMaterial = shaderMaterial(
  {
    uTime: 0,
    uLocalCamPos: new THREE.Vector3(),
    uBaseColor: new THREE.Color('#7780ff'),
    uSecondaryColor: new THREE.Color('#00e5ff'),
    uBrightness: 1.42,
    uFoldSteps: 6,
    uKifsScale: 1.26,
    uKifsOffset: 8.9,
    uTextureScale: 1.45,
    uLightSpeed: 5.5,
    uMaxSteps: 110,
    uAutoRotationSpeed: 1.0,
    uShellInner: 1.82,
  },
  reactorVert,
  reactorFrag,
);

extend({ ReactorMaterial });

declare module '@react-three/fiber' {
  interface ThreeElements {
    reactorMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
