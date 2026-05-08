// Lattice variant — sphere-mounted port of an AABB-bounded volumetric
// raymarch through a repeating wrapped-grid SDF. The cube IS the
// silhouette; the lattice (`min(max(x,y), min(max(y,z), max(x,z)))`
// of a wrapped position) traces the cell walls inside the cube,
// reading as faceted shells stacked on themselves.
//
// Original was shadertoy-style (full-screen quad, ortho camera,
// shader-driven mouse rotation). Adapted to ORBIS's variant pattern:
// ray origin = uLocalCamPos, ray dir = normalized(vLocalPosition -
// uLocalCamPos), so R3F's mesh.rotation handles user drag + auto
// rotation and the AABB stays fixed in object space.

uniform float uTime;
uniform vec3 uLocalCamPos;
uniform vec3 uPrimaryColor;
uniform vec3 uSecondaryColor;

uniform float uCubeSize;       // half-extent of the AABB
uniform float uGridScale;      // spatial frequency of the lattice
uniform float uDistortion;     // sin/cos warp on the lattice
uniform float uGlow;           // multiplier on the volume accumulator
uniform vec3  uColorOffset;    // RGB shift in the per-step cosine palette

uniform vec3  uClickDir;
uniform float uClickStrength;

varying vec3 vLocalPosition;

// AABB intersection. Returns (tNear, tFar); negative tFar = miss.
// 0.99 / rd matches the source — keeps the ray slightly off the
// box face to avoid acne on grazing angles.
vec2 intersectBox(vec3 ro, vec3 rd, vec3 extents) {
  vec3 m = 0.99 / rd;
  vec3 n = m * ro;
  vec3 k = abs(m) * extents;
  vec3 t1 = -n - k;
  vec3 t2 = -n + k;
  float tNear = max(max(t1.x, t1.y), t1.z);
  float tFar  = min(min(t2.x, t2.y), t2.z);
  if (tNear > tFar || tFar < 0.0) return vec2(-1.0);
  return vec2(max(tNear, 0.0), tFar);
}

void main() {
  vec3 ro = uLocalCamPos;
  vec3 rd = normalize(vLocalPosition - uLocalCamPos);

  vec3 boxExtents = vec3(uCubeSize);
  vec2 hit = intersectBox(ro, rd, boxExtents);

  // Outside the cube → discard (nothing to draw on this fragment).
  // Pixels behind the cube along the ray hit too, so this is the
  // primary silhouette gate.
  if (hit.y < 0.0) discard;

  float t = hit.x;
  float tMax = hit.y;

  // Pre-loop constants — same shape as the source so the colour
  // shimmer matches when uColorOffset/uTime/uGridScale all match.
  vec3 colorBase = uColorOffset + (uTime * 2.0) + 0.09;

  vec3 accum = vec3(0.0);

  // 100-step volume march. Compiler-friendly integer counter; early
  // out via ``t >= tMax``. Same loop budget as the source — the
  // distinct visual identity is the cell-wall traversal at every step.
  for (int i = 0; i < 100; i++) {
    if (t >= tMax) break;
    vec3 pos = ro + rd * t;
    vec3 q = pos * uGridScale;

    // Wrapped-grid SDF: fract → triangle wave → cell-min-of-edges.
    // This is what carves the lattice shells.
    vec3 pWrap = abs(fract(q * 0.25) * 4.0 - 2.0);
    float dStruct = min(
      max(pWrap.x, pWrap.y),
      min(max(pWrap.y, pWrap.z), max(pWrap.x, pWrap.z))
    ) - 1.0;

    float distortion = 0.0;
    // Skip the trig pair when distortion is off — this saves a real
    // chunk on the inner loop on lower-end GPUs.
    if (uDistortion > 0.001) {
      distortion = uDistortion * dot(sin(q * 1.2), cos(q.yzx * 1.0));
    }

    float shape = abs(dStruct) + distortion;
    float stepSize = max(0.01, 0.7 * abs(shape));
    stepSize = min(stepSize, tMax - t);

    // Per-step palette: cosine over loop index gives the rainbow
    // shimmer; multiply by a depth-weighted primary↔secondary mix so
    // the orb's voice-state colours propagate through the cube depth.
    float depthT = float(i) / 100.0;
    vec3 voiceMix = mix(uSecondaryColor, uPrimaryColor, depthT);
    vec3 palette = (0.5 + 0.5 * cos(0.15 * float(i) + colorBase)) * voiceMix;

    // Click reactivity — same shape as Tetra: samples whose direction
    // from origin aligns with uClickDir get a glow boost. No flat
    // halo on the cube faces; the reactivity reads inside the lattice.
    float plen = length(pos);
    vec3 pDir = (plen > 1e-4) ? pos / plen : vec3(0.0);
    float clickBoost = uClickStrength * smoothstep(0.55, 1.0, dot(pDir, uClickDir));

    // Edge fade so the slab right at the entry plane doesn't pop
    // when zooming or moving.
    float edgeFade = smoothstep(0.0, 0.5, t - hit.x);
    float weight = stepSize * edgeFade * (0.049 / (abs(shape) + 0.09));

    accum += palette * weight * (1.0 + clickBoost * 2.0);
    if (clickBoost > 0.001) {
      accum += uPrimaryColor * weight * clickBoost * 0.4;
    }

    t += stepSize;
  }

  // Smooth tone-mapping; tanh keeps highlights from clipping while
  // letting the lattice cores look bright.
  vec3 finalColor = tanh(accum * uGlow * 1.4);

  // Alpha mirrors luminance so the cube blends into the canvas
  // instead of carrying a black box around its silhouette.
  float maxLuma = max(finalColor.r, max(finalColor.g, finalColor.b));
  float alpha = clamp(maxLuma * 1.5, 0.0, 1.0);

  gl_FragColor = vec4(finalColor, alpha);
}
