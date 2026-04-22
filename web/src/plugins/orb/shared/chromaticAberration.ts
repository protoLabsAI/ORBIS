import * as THREE from 'three';
import { Effect } from 'postprocessing';

/**
 * Custom luma-masked chromatic aberration. Identical to the
 * ShaderPass in the imperative viz.js — offsets only apply where the
 * pixel is already bright (luma > threshold), so the dark orb
 * background doesn't get rainbow halos.
 *
 * Meant to be used as a child of `<EffectComposer>` from
 * `@react-three/postprocessing`. Instance is created once and reused;
 * update `uniforms.get('uAmount').value` from a `useFrame` callback.
 */

const fragmentShader = /* glsl */ `
  uniform float uAmount;

  void mainImage(const in vec4 inputColor, const in vec2 uv, out vec4 outputColor) {
    float luma = max(inputColor.r, max(inputColor.g, inputColor.b));
    float mask = smoothstep(0.01, 0.1, luma);
    vec2 offset = (uv - 0.5) * uAmount;
    float r = texture2D(inputBuffer, uv + offset).r;
    float g = texture2D(inputBuffer, uv).g;
    float b = texture2D(inputBuffer, uv - offset).b;
    vec3 aberrated = vec3(r, g, b);
    outputColor = vec4(mix(inputColor.rgb, aberrated, mask), inputColor.a);
  }
`;

export class LumaChromaticAberrationEffect extends Effect {
  constructor({ amount = 0.025 }: { amount?: number } = {}) {
    super('LumaChromaticAberration', fragmentShader, {
      uniforms: new Map([['uAmount', new THREE.Uniform(amount)]]),
    });
  }

  setAmount(v: number): void {
    const u = this.uniforms.get('uAmount');
    if (u) u.value = v;
  }
}
