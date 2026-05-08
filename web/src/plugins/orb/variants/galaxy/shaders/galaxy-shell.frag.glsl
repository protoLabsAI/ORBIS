// Galaxy shell fragment — Fresnel-based glass envelope. Two
// instances of this material are stacked (BackSide + FrontSide,
// additive blending) to produce the depth-cued "glass orb" effect.
// Shares sphere.vert.glsl's vNormal + vViewPosition varyings.

uniform vec3 uColor;
uniform float uOpacity;

varying vec3 vNormal;
varying vec3 vViewPosition;

void main() {
  float fresnel = pow(
    1.0 - dot(normalize(vNormal), normalize(vViewPosition)),
    2.5
  );
  gl_FragColor = vec4(uColor, fresnel * uOpacity);
}
