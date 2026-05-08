// Tetra variant — sphere-mounted port of a tetrahedron + spherical-
// inversion fractal raymarch. The original (shadertoy-style, full-
// screen quad, ortho camera) ran its own camera math; here we drop
// into the standard ORBIS sphere-mesh pattern: ray origin =
// uLocalCamPos, ray dir = normalize(vLocalPosition - uLocalCamPos),
// bounded by the unit-sphere intersection so the orb composes with
// the atmosphere shell + breath/audio scale + voice-state crossfade.
//
// The fractal generation (tetrahedron SDF intersected with a
// spherical-inversion fold loop) is preserved verbatim — that's the
// distinctive look. The reflection bounce was dropped: the volumetric
// glow accumulator is what reads, and an extra estimateNormal at every
// surface hit (4 SDF calls each) was the bulk of the cost.

uniform float uTime;
uniform vec3 uLocalCamPos;
uniform vec3 uPrimaryColor;
uniform vec3 uSecondaryColor;
uniform float uShapeSize;
uniform float uIterations;
uniform vec3 uFold;
uniform float uGlowIntensity;
uniform float uGlowBase;
uniform float uInternalAnim;
uniform vec3 uClickDir;
uniform float uClickStrength;

varying vec3 vLocalPosition;
varying vec3 vNormal;
varying vec3 vViewPosition;

// Per-march state. Set by sceneSDF, perturbed each step in the march
// loop — same pattern as the source shader's `g_fractalTint`.
vec3 g_tint;

mat2 rot2(float a) {
  float c = cos(a), s = sin(a);
  return mat2(c, -s, s, c);
}

// Tetrahedron SDF — bounding shape. The sqrt(3) divisor is the
// correct normaliser for a regular tetrahedron, otherwise the march
// step overshoots and you get banded surfaces.
float sdTetra(vec3 p, float r) {
  float d = max(
    max(-p.x - p.y - p.z, p.x + p.y - p.z),
    max(-p.x + p.y + p.z, p.x - p.y + p.z)
  );
  return (d - r) / sqrt(3.0);
}

// Intersect the spherical-inversion fractal with a tetrahedron bound.
// Rotation matrices are passed in so the per-step trig stays out of
// the inner loop. g_tint is written so the march can read it back to
// drive the volumetric glow color.
float sceneSDF(
  vec3 p,
  mat2 rotAnim, mat2 rotAnim07,
  mat2 rotXY, mat2 rotXZ
) {
  vec3 lp = p;
  lp.xz *= rotAnim;
  lp.xy *= rotAnim07;

  float bound = sdTetra(lp, uShapeSize);

  vec4 q = vec4(lp, 1.0);
  for (int k = 0; k < 10; k++) {
    if (float(k) >= uIterations) break;
    // Spherical inversion fold — curls the fractal into organic
    // inner structures that tetrahedron-only folds can't produce.
    float r2 = dot(q.xyz, q.xyz);
    float fold = max(1.4 / max(r2, -0.8), 1.1);
    q *= fold;
    // Space fold offset by uFold (XYZ tunables on the panel).
    q.xyz = abs(q.xyz) - uFold;
    q.xy *= rotXY;
    q.xz *= rotXZ;
    q *= 1.4;
  }

  g_tint = q.xyz * log(q.w + 1.0);

  float surf = (length(q.xyz) - 1.2) / q.w;
  return max(bound, surf);
}

vec2 sphereBounds(vec3 o, vec3 d, float r) {
  float b = dot(o, d);
  float c = dot(o, o) - r * r;
  float disc = b * b - c;
  if (disc < 0.0) return vec2(-1.0);
  float root = sqrt(disc);
  return vec2(-b - root, -b + root);
}

float hash(vec2 p) {
  return fract(sin(dot(p, vec2(12.9098, 78.013))) * 43758.5453);
}

void main() {
  vec3 origin = uLocalCamPos;
  vec3 dir = normalize(vLocalPosition - uLocalCamPos);

  vec2 limits = sphereBounds(origin, dir, 2.0);
  // Camera inside the sphere → near intersection sits behind us; clamp
  // start to camera origin (0) so the ray marches forward, not backward.
  limits.x = max(limits.x, 0.0);
  if (limits.y < 0.0) discard;

  float t = uTime * uInternalAnim;
  mat2 rotAnim   = rot2(t);
  mat2 rotAnim07 = rot2(t * 0.7);
  mat2 rotXY     = rot2(0.7 + sin(uTime * 0.05) * 0.1);
  mat2 rotXZ     = rot2(0.5);

  // Pixel-stable jitter — breaks volumetric banding without animating
  // the noise frame-to-frame.
  float jitter = hash(gl_FragCoord.xy) * 0.05;
  float depth = limits.x + jitter;

  vec3 accum = vec3(0.0);
  int hitCount = 0;

  for (int i = 0; i < 96; i++) {
    if (depth > limits.y) break;
    vec3 p = origin + dir * depth;
    float sd = sceneSDF(p, rotAnim, rotAnim07, rotXY, rotXZ);

    // Tint perturbation per step — chaotic but deterministic; squaring
    // keeps it positive for the colour mix below.
    g_tint = cos(g_tint * uGlowBase);
    g_tint *= g_tint;

    if (i > 1) {
      float distSq = dot(p, p);
      float att = exp(-distSq * 0.25);
      // Mix primary↔secondary along the tint magnitude so the
      // palette propagates through the glow rather than being a flat
      // emission colour.
      float mixT = clamp(length(g_tint) * 0.6, 0.0, 1.0);
      vec3 emission = mix(uSecondaryColor, uPrimaryColor, mixT);
      accum += uGlowIntensity * g_tint * att * emission;
    }

    if (sd < 0.0002) {
      // Brief boost on surface hit, then push past — no reflection
      // (skipping the 4-call estimateNormal keeps the march cheap).
      accum *= 1.18;
      depth += 0.04;
      hitCount++;
      if (hitCount > 2) break;
    }

    depth += sd * 0.8;
  }

  // Edge AA via the sphere normal — same shape as fractal/nebula so
  // the silhouette matches the rest of the orb family.
  vec3 normal = normalize(vNormal);
  vec3 viewDir = normalize(vViewPosition);
  float facingRatio = max(dot(normal, viewDir), 0.0);
  float edgeAA = smoothstep(0.0, 0.05, facingRatio);

  // Contrast curve from the source shader. Squaring darkens the
  // mid-tones so the bright fractal cores pop against the rim.
  vec3 finalColor = accum * accum;
  finalColor = clamp(finalColor, 0.0, 1.0);
  finalColor *= edgeAA;

  float maxLuma = max(finalColor.r, max(finalColor.g, finalColor.b));
  float alpha = clamp(maxLuma * 1.5, 0.0, 1.0) * edgeAA;

  // Click bloom — tight cone on uClickDir; same envelope as fractal.
  vec3 localNormal = normalize(vLocalPosition);
  float clickBoost = smoothstep(0.75, 1.0, dot(localNormal, uClickDir)) * uClickStrength;
  finalColor += uPrimaryColor * clickBoost * 0.9;
  alpha = clamp(alpha + clickBoost * 0.4, 0.0, 1.0);

  gl_FragColor = vec4(finalColor, alpha);
}
