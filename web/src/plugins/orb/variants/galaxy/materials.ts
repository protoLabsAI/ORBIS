import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import sphereVert from '../../shared/shaders/sphere.vert.glsl';
import shellFrag from './shaders/galaxy-shell.frag.glsl';
import plasmaFrag from './shaders/galaxy-plasma.frag.glsl';
import particlesVert from './shaders/galaxy-particles.vert.glsl';
import particlesFrag from './shaders/galaxy-particles.frag.glsl';

/**
 * Galaxy = three layered shaders, glued together by GalaxyVariant:
 *
 *   GalaxyShellMaterial    — Fresnel "glass" envelope (used twice,
 *                            BackSide + FrontSide, additive blend)
 *   GalaxyPlasmaMaterial   — domain-warped 3D Simplex/FBM noise
 *                            inside the orb. The visual identity.
 *   GalaxyParticlesMaterial — point sprites for the dust field
 *
 * The three are siblings under a parent <group> so a single
 * mesh.rotation drives all of them, and a single mesh.scale lives
 * on the group too.
 */

export const GalaxyShellMaterial = shaderMaterial(
  {
    uColor: new THREE.Color('#0066ff'),
    uOpacity: 0.41,
  },
  sphereVert,
  shellFrag,
);

export const GalaxyPlasmaMaterial = shaderMaterial(
  {
    uTime: 0,
    uScale: 0.2,
    uBrightness: 1.31,
    uThreshold: 0.09,
    uColorDeep: new THREE.Color('#001433'),
    uColorMid: new THREE.Color('#0084ff'),
    uColorBright: new THREE.Color('#00ffe1'),
    uPrimaryColor: new THREE.Color('#7dd3fc'),
    uSecondaryColor: new THREE.Color('#fb7185'),
    uVoiceMix: 0.25,
    uClickDir: new THREE.Vector3(0, 0, 1),
    uClickStrength: 0,
  },
  sphereVert,
  plasmaFrag,
);

export const GalaxyParticlesMaterial = shaderMaterial(
  {
    uTime: 0,
    uColor: new THREE.Color('#ffffff'),
  },
  particlesVert,
  particlesFrag,
);

extend({ GalaxyShellMaterial, GalaxyPlasmaMaterial, GalaxyParticlesMaterial });

declare module '@react-three/fiber' {
  interface ThreeElements {
    galaxyShellMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
    galaxyPlasmaMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
    galaxyParticlesMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
