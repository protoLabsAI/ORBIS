import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import liquidVert from '../../shared/shaders/sphere.vert.glsl';
import liquidFrag from './shaders/liquid.frag.glsl';

/**
 * Liquid core material. Hard-surface raymarch — the only ORBIS
 * variant that finds a single surface hit rather than accumulating
 * volume — with a domain-warped height-field displacement, a
 * 4-colour procedural palette, and Phong-style shading
 * (diffuse + fill + specular + rim). Reads as mercury / oil-puddle.
 */
export const LiquidMaterial = shaderMaterial(
  {
    uTime: 0,
    uLocalCamPos: new THREE.Vector3(),
    uPrimaryColor: new THREE.Color('#7dd3fc'),
    uSecondaryColor: new THREE.Color('#fb7185'),

    uSphereSize: 1.0,
    uWarpAmp: 0.6,
    uWarpFalloff: 1.2,
    uWarpStartFreq: 6.0,
    uWarpSteps: 10.0,
    uWarpVelocity: -0.4,
    uNoiseContrast: 0.6,
    uHeightAmp: 0.35,

    uColor1: new THREE.Color('#002aff'),
    uColor2: new THREE.Color('#0040ff'),
    uColor3: new THREE.Color('#4400ff'),
    uColor4: new THREE.Color('#330aff'),

    uLightDir: new THREE.Vector3(1.0, 1.0, -1.0),
    uFillDir: new THREE.Vector3(-1.0, -0.5, -0.5),
    uAmbient: 0.09,
    uDiffuse: 0.6,
    uFillLight: 0.2,
    uSpecularPower: 32.0,
    uSpecularIntensity: 0.5,

    uClickDir: new THREE.Vector3(0, 0, 1),
    uClickStrength: 0,
  },
  liquidVert,
  liquidFrag,
);

extend({ LiquidMaterial });

declare module '@react-three/fiber' {
  interface ThreeElements {
    liquidMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
