import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import spectrumVert from '../../shared/shaders/sphere.vert.glsl';
import spectrumFrag from './shaders/spectrum.frag.glsl';

/**
 * Spectrum core material. Volumetric raymarch through two smooth-min-
 * blended SDFs with nested sine-wave turbulence; emission accumulator
 * uses a phase-shifted cosine palette to produce a rainbow gradient
 * through the volume.
 */
export const SpectrumMaterial = shaderMaterial(
  {
    uTime: 0,
    uLocalCamPos: new THREE.Vector3(),
    uPrimaryColor: new THREE.Color('#7dd3fc'),
    uSecondaryColor: new THREE.Color('#fb7185'),
    uFractalScale: 1.0,
    uFadeOuter: 2.56,
    uFadeInner: 2.55,
    uSmoothing: 1.55,
    uColorPhases: new THREE.Vector4(5.8, 4.1, 2.8, 0.2),
    uGlow: 1.0,
    uClickDir: new THREE.Vector3(0, 0, 1),
    uClickStrength: 0,
  },
  spectrumVert,
  spectrumFrag,
);

extend({ SpectrumMaterial });

declare module '@react-three/fiber' {
  interface ThreeElements {
    spectrumMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
