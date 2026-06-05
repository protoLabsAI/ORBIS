// Geode — a glowing octahedron of volumetric plasma with neon wireframe edges.
// Adapted from the screen-space "octa plasma" prototype to ORBIS's sphere-mesh
// raymarch: the sphere mesh is just the proxy the ray passes through; the actual
// shape is an octahedron SDF at the mesh origin. We march from uLocalCamPos,
// skip empty space by the octahedron distance, and accumulate plasma + edge
// "frame" lines inside. The mesh rotation tumbles it; Bloom (GeodePost) glows.
//
// Unlike Flux, brightness here is geometry-driven (accumulation ÷ stepLen²), not
// an exp(t) term, so the orb camera distance doesn't inflate it.

uniform float uTime;
uniform vec3 uLocalCamPos;
uniform vec3 uColorPlasma; // state primary
uniform vec3 uColorFrame;  // state secondary (wireframe edges)
uniform float uShapeSize;
uniform float uShapeStretch;
uniform float uPlasmaDensity;
uniform float uPlasmaScale; // maps our small mesh coords into the noise domain
uniform float uBrightness;
uniform float uShellInner;  // raymarch proxy-sphere radius
uniform float uMaxSteps;

varying vec3 vLocalPosition;
varying vec3 vNormal;
varying vec3 vViewPosition;

const mat3 NOISE_TRANSFORM = mat3(
  -0.21,  1.33,  0.52,
   0.72,  0.52,  1.67,
   0.77,  0.49,  0.18
);

float calcNoise(vec3 p) {
  vec3 a = cos(NOISE_TRANSFORM * p);
  vec3 b = sin(17.1 * p * NOISE_TRANSFORM);
  return dot(a, b);
}

vec2 intersectSphere(vec3 ro, vec3 rd, float radius) {
  float b = dot(ro, rd);
  float c = dot(ro, ro) - radius * radius;
  float h = b * b - c;
  if (h < 0.0) return vec2(-1.0);
  h = sqrt(h);
  return vec2(-b - h, -b + h);
}

void main() {
  vec3 ro = uLocalCamPos;
  vec3 rd = normalize(vLocalPosition - uLocalCamPos);

  vec2 hit = intersectSphere(ro, rd, uShellInner);
  if (hit.y < 0.0) {
    gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
    return;
  }

  float tEnter = max(0.0, hit.x);
  float tExit = hit.y;
  float t = tEnter;
  vec4 acc = vec4(0.0);

  for (int i = 0; i < 200; i++) {
    if (float(i) >= uMaxSteps) break;
    vec3 pos = ro + rd * t;

    vec3 shapePos = pos;
    shapePos.y *= uShapeStretch;
    // Octahedron distance field (|x|+|y|+|z| - size), softened.
    float bounds = ((abs(shapePos.x) + abs(shapePos.y) + abs(shapePos.z)) - uShapeSize) * 0.45;

    if (bounds > 0.005) {
      // Outside the gem — skip ahead by the (positive) distance.
      t += max(0.01, bounds);
    } else {
      vec3 np = pos * uPlasmaScale;
      float timeOffset = uTime * 0.1;
      float wave = sin(np.z * 3.1 + np.x * 1.7 + timeOffset) * -0.076 - 0.011;
      float nHi = calcNoise(np + uTime * 0.2);
      float nLo = calcNoise(np / 4.5 + uTime * 0.13) * 0.8;
      float noiseVal = wave + abs(nHi - nLo);

      float surfaceDist = abs(bounds);
      float edgeDist = min(abs(shapePos.x), min(abs(shapePos.y), abs(shapePos.z)));
      float edgeProximity = smoothstep(0.15, 0.0, edgeDist) * smoothstep(0.25, 0.0, surfaceDist);
      float stepWithNoise = 0.02 + noiseVal * uPlasmaDensity;
      float stepLen = mix(stepWithNoise, 0.015, edgeProximity);

      float lineThickness = 0.04;
      float lineIntensity = smoothstep(lineThickness, 0.01, edgeDist) * smoothstep(0.02, -0.1, surfaceDist);

      vec4 frameColor = vec4(uColorFrame, 1.0) * lineIntensity * 12.1;
      vec4 baseColor = vec4(-3.0, 0.80, 2.8, 1.1);
      vec4 glowColor = baseColor * cos(float(i) * 0.034) + float(i) * 0.017;
      glowColor.rgb *= uColorPlasma;

      acc += (glowColor + frameColor) / (stepLen * stepLen * 150000.0);
      t += max(0.008, stepLen);
    }
    if (t > tExit) break;
  }

  gl_FragColor = vec4(acc.rgb * uBrightness, 1.0);
}
