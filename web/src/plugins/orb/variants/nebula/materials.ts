import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import vert from '../../shared/shaders/sphere.vert.glsl';
import frag from './shaders/nebula.frag.glsl';

export const NebulaMaterial = shaderMaterial(
  {
    uTime: 0,
    uLocalCamPos: new THREE.Vector3(),
    uPrimaryColor: new THREE.Color('#c084fc'),
    uSecondaryColor: new THREE.Color('#38bdf8'),
    uDensity: 1.2,
    uCloudScale: 1.8,
    uCloudiness: 0.9,
    uDrift: 0.5,
    uSoftness: 0.6,
    uClickDir: new THREE.Vector3(0, 0, 1),
    uClickStrength: 0,
  },
  vert,
  frag,
);

extend({ NebulaMaterial });

declare module '@react-three/fiber' {
  interface ThreeElements {
    nebulaMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
