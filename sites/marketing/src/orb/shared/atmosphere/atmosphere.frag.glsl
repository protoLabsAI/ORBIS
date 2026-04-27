uniform vec3 uColor;
uniform vec3 uColorSecondary;
uniform float uGlow;
uniform float uLevel;
uniform vec3 uClickDir;
uniform float uClickStrength;

varying vec3 vNormal;
varying vec3 vViewPosition;
varying vec3 vLocalPos;

void main() {
  vec3 normal = normalize(vNormal);
  vec3 viewDir = normalize(vViewPosition);
  float vdn = max(dot(normal, viewDir), 0.0);
  float edgeFade = smoothstep(0.0, 0.15, vdn);
  float innerFadePoint = clamp(1.0 - uLevel, 0.0, 0.99);
  float centerFade = smoothstep(1.0, innerFadePoint, vdn);
  float alpha = edgeFade * centerFade * uGlow;

  // Radial halo gradient — secondary at the limb, primary pushing inward.
  // Wider smoothstep = more of the ring shows the transition band, not
  // just a hard split between the two hues.
  float gradT = smoothstep(0.0, 0.85, vdn);
  vec3 haloColor = mix(uColorSecondary, uColor, gradT);

  // Click bloom — matched to the tight cone in the core shader; lit
  // area on the halo is a focused spot rather than a wide arc.
  float clickBoost = smoothstep(0.6, 1.0, dot(normalize(vLocalPos), uClickDir)) * uClickStrength;
  alpha *= (1.0 + clickBoost * 3.0);
  haloColor = mix(haloColor, uColor * 1.25, clickBoost * 0.7);

  gl_FragColor = vec4(haloColor, alpha);
}
