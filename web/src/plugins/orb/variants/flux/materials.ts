import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import fluxVert from '../../shared/shaders/sphere.vert.glsl';
import fluxFrag from './shaders/flux.frag.glsl';

/**
 * Flux material — a fluid, pulsing volumetric SDF fractal raymarched inside the
 * orb's shell. Same family as Reactor; the emission colour (`uColorBase`) is
 * NOT hue-spun in the shader — the CPU slowly cycles it between palette colours
 * (see FluxVariant) and bakes brightness into its magnitude.
 */
export const FluxMaterial = shaderMaterial(
  {
    uTime: 0,
    uLocalCamPos: new THREE.Vector3(),
    uColorBase: new THREE.Color('#ff7a18'),
    uIterations: 20,
    uDistortion: 2.2,
    uPulseIntensity: 2.5,
    uFractalScale: 0.16,
    uRadius: 1.85,
  },
  fluxVert,
  fluxFrag,
);

extend({ FluxMaterial });

declare module '@react-three/fiber' {
  interface ThreeElements {
    fluxMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
