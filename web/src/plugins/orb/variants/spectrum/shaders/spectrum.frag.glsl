// Spectrum variant — sphere-mounted port of an organic
// volumetric raymarch through two smooth-min-blended SDFs with
// nested sine-wave turbulence and a phase-shifted cosine rainbow
// palette. The "rainbow" feel comes from the per-step
// `1 + cos(depth + uColorPhases)` accumulator; the soft-boundary
// fade between uFadeInner and uFadeOuter gives the halo silhouette.
//
// Adapted from a shadertoy-style sketch (full-screen quad + ortho
// camera + screen-space rotation matrix). Same playbook as tetra/
// lattice: ray origin = uLocalCamPos, ray dir = normalize
// (vLocalPosition - uLocalCamPos), R3F mesh.rotation handles
// auto-rotate + drag.

uniform float uTime;
uniform vec3  uLocalCamPos;
uniform vec3  uPrimaryColor;
uniform vec3  uSecondaryColor;

uniform float uFractalScale;     // shrinks/expands the SDF coordinate space
uniform float uFadeOuter;        // soft-fade outer radius
uniform float uFadeInner;        // soft-fade inner radius (where contribution = 1)
uniform float uSmoothing;        // smooth-min blend k between the two spheres
uniform vec4  uColorPhases;      // RGBA phase offsets for the cosine palette
uniform float uGlow;             // multiplier on the final accumulator

uniform vec3  uClickDir;
uniform float uClickStrength;

varying vec3 vLocalPosition;

// Polynomial smooth minimum — organic blend between two SDFs.
float smin(float a, float b, float k) {
  float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
  return mix(b, a, h) - k * h * (1.0 - h);
}

// Two-sphere blend with nested sine-wave turbulence on both
// sample positions. The non-uniform iteration step (2.3 → 6.0
// step 1.1) is kept verbatim — that irregularity is what makes
// the turbulence feel organic instead of grid-like.
float evaluateScene(vec3 p, float t, out vec3 secondaryPosOut) {
  vec3 primaryPos   = p * uFractalScale;
  vec3 secondaryPos = p * uFractalScale;

  for (float iteration = 2.3; iteration <= 6.0; iteration += 1.1) {
    float secondaryScale = iteration * 0.3;
    secondaryPos += sin(0.6 * t + primaryPos.zxy * secondaryScale) * 0.4;
    primaryPos   += sin(t + primaryPos.yzx * iteration) * 0.25;
  }

  secondaryPosOut = secondaryPos;

  float sphereA = length(primaryPos + 1.0) - 2.0;
  float sphereB = length(secondaryPos - 1.3) - 2.9;
  float blended = smin(sphereA, sphereB, uSmoothing);

  // Volume contribution: thin shell around the blended surface.
  // Divide by uFractalScale so the SDF stays correctly normalised
  // when uFractalScale changes (otherwise denser scales overshoot).
  return abs(blended) * 0.1 / uFractalScale;
}

// Hash for jitter — breaks up volumetric banding.
float hash(vec2 p) {
  return fract(sin(dot(p, vec2(12.8998, 78.233))) * 43758.5453);
}

// Cosine-palette emission. Phase shifts in uColorPhases pull the
// rainbow toward different hues; the per-pixel z-depth is what
// produces the rainbow gradient through the volume.
vec3 paletteFor(float depth) {
  // The original returns vec4 + uColorPhases (RGBA); we only need
  // RGB for the additive accumulator.
  return 1.0 + cos(depth + uColorPhases.rgb);
}

void main() {
  vec3 ro = uLocalCamPos;
  vec3 rd = normalize(vLocalPosition - uLocalCamPos);

  // Pixel-stable jitter to break banding.
  float t = -0.015 * hash(gl_FragCoord.xy);

  vec3 accum = vec3(0.0);

  // Per-pixel ray budget. The source ran from a camera at z=-4.5 with
  // a 10-unit cap; ORBIS's camera sits at z=13 in object space and the
  // bounding-sphere fast-forward jumps the ray from there to fadeOuter,
  // which already eats ~10 units before the orb is reached. Raise the
  // cap to comfortably cover camera distance + sphere diameter + a
  // little slack.
  const float T_MAX = 25.0;

  for (int step = 0; step < 90; step++) {
    if (t > T_MAX) break;
    vec3 pos = ro + rd * t;
    float distFromCenter = length(pos);

    // Bounding-sphere fast-forward — skip expensive turbulence math
    // when we're outside the visible volume. The 0.01 offset lets
    // the smoothstep below still feather correctly at the edge.
    if (distFromCenter > uFadeOuter + 0.01) {
      t += distFromCenter - uFadeOuter;
      continue;
    }

    vec3 secondaryPos;
    float sceneDist = evaluateScene(pos, uTime, secondaryPos);

    float stepSize = 0.012 + sceneDist;
    t += stepSize;

    // Soft containment — smooth fade from outer (0) to inner (1)
    // radius so the orb blends into space instead of clipping at a
    // hard sphere edge.
    float radialFade = smoothstep(uFadeOuter, uFadeInner, distFromCenter);

    // Per-step rainbow palette. uColorPhases is the deployer's hue
    // shift; the depth-mod-by-z gives the rainbow gradient.
    vec3 baseColor = paletteFor(pos.z);

    // Lightly tint with primary↔secondary so voice-state colour
    // crossfades come through without overpowering the rainbow.
    // 25% mix keeps the spectrum identity intact in idle / neutral
    // states; state changes still register visually.
    vec3 voiceTint = mix(uSecondaryColor, uPrimaryColor, 0.5);
    baseColor = mix(baseColor, baseColor * voiceTint * 2.0, 0.25);

    // Click reactivity — same in-march directional boost the other
    // sphere-mounted variants use. Samples whose direction from
    // origin aligns with uClickDir glow brighter.
    float plen = length(pos);
    vec3 pDir = (plen > 1e-4) ? pos / plen : vec3(0.0);
    float clickBoost = uClickStrength * smoothstep(0.55, 1.0, dot(pDir, uClickDir));

    float weight = (1.0 / max(stepSize, 1e-4)) * radialFade;
    accum += baseColor * weight * (1.0 + clickBoost * 1.5);
  }

  // Tone-map. The original divides by 8000 × screen-vignette;
  // sphere-mounted geometry has no usable screen-uv so we drop
  // the vignette and use a softer divisor instead.
  vec3 tone = accum * uGlow / 8000.0;

  // tanh keeps highlights from clipping while preserving bright
  // cores; clamp the input so exp() can't blow up on mobile GPUs.
  vec3 clamped = clamp(tone, -20.0, 20.0);
  vec3 e2x = exp(2.0 * clamped);
  vec3 finalColor = (e2x - 1.0) / (e2x + 1.0);
  finalColor = clamp(finalColor, 0.0, 1.0);

  // Alpha mirrors luminance so the orb blends into the canvas
  // background instead of carrying a black box around its silhouette.
  float maxLuma = max(finalColor.r, max(finalColor.g, finalColor.b));
  float alpha = clamp(maxLuma * 1.5, 0.0, 1.0);

  gl_FragColor = vec4(finalColor, alpha);
}
