// Flux — a fluid, pulsing volumetric SDF fractal glowing inside the orb's
// sphere. Adapted from the screen-space "quantum orb" prototype to ORBIS's
// sphere-mesh raymarch: ray origin = uLocalCamPos, direction toward the
// fragment; the mesh rotation spins it. The prototype's continuous hue-rotation
// is removed — emission uses uColorBase directly, which the CPU cycles between
// 2–3 palette colours. Bloom (FluxPost) adds the outer glow.

uniform float uTime;
uniform vec3 uLocalCamPos;
uniform vec3 uColorBase; // cycled literal colour from the CPU
uniform float uIterations;
uniform float uDistortion;
uniform float uPulseIntensity;
uniform float uFractalScale; // maps the orb's volume into the fractal domain
uniform float uRadius; // inner raymarch volume radius

varying vec3 vLocalPosition;
varying vec3 vNormal;
varying vec3 vViewPosition;

float gPhase = 1.0;

mat2 rot2(float a) {
  float s = sin(a), c = cos(a);
  return mat2(c, s, -s, c);
}

float dither(vec2 p) {
  vec3 h = fract(vec3(p.xyx) * 0.8431);
  h += dot(h, h.yzx + 48.55);
  return fract((h.x + h.y) * h.z);
}

vec2 intersectSphere(vec3 ro, vec3 rd, float radius) {
  float b = dot(ro, rd);
  float c = dot(ro, ro) - radius * radius;
  float h = b * b - c;
  if (h < 0.0) return vec2(-1.0);
  h = sqrt(h);
  return vec2(-b - h, -b + h);
}

float sceneSDF(vec3 p) {
  p.yz *= rot2(0.4);
  p.xz *= rot2(uTime * 0.4);
  float minD = 125.4;
  p *= uFractalScale;
  for (int i = 0; i < 30; ++i) {
    if (float(i) >= uIterations) break;
    float fi = float(i) + 55.0;
    p.xy = abs(p.xy);
    p.xy = sin(p.xy * uDistortion);
    p.xy *= rot2(0.5);
    p.xz *= rot2(0.7);
    float d = length(p.xy) + 0.07;
    if (i > 0) minD = min(minD, d);
    if (minD == d) gPhase = fi;
  }
  return minD * 0.3;
}

void main() {
  vec3 ro = uLocalCamPos;
  vec3 rd = normalize(vLocalPosition - uLocalCamPos);

  vec2 hit = intersectSphere(ro, rd, uRadius);
  if (hit.y < 0.0) {
    gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
    return;
  }

  float tEnter = max(0.0, hit.x);
  float tExit = hit.y;
  float t = tEnter + dither(gl_FragCoord.xy + uTime) * 0.1;
  vec3 acc = vec3(0.0);

  for (int s = 0; s < 75; s++) {
    vec3 pos = ro + rd * t;
    float d = abs(sceneSDF(pos));
    t += max(0.015, d);
    if (t > tExit) break;

    vec3 tint = uColorBase;
    // Distance-into-volume, NOT absolute ray length: the orb camera sits ~13
    // units out (vs the prototype's ~4.5), so exp(0.14 * t) inflated the glow
    // ~3×. Measure the exponential build-up from the volume entry instead.
    tint *= exp(0.14 * (t - tEnter));
    tint *= exp(1.0 * length(pos));
    tint /= (80.0 + d * 1500.0);

    float wave = (length(pos) * 0.05) - (uTime * 0.03) + (gPhase * 0.56);
    float pulse = abs(pow(abs(fract(wave) - 0.50) * 2.0, 68.4));
    tint *= (0.2 + pulse * uPulseIntensity);

    acc += tint;
    acc += exp(-4.9 * length(pos)) * 0.07;
  }

  gl_FragColor = vec4(acc, 1.0);
}
