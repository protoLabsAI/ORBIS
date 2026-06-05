// Disco ball — a faceted mirror sphere whose facets reflect a procedural neon
// environment. Adapted from the screen-space raytracer prototype to ORBIS's
// sphere-mesh surface shading: the fragment is a point on the orb sphere, we
// quantise its normal into mirror facets (lat/long tiles), reflect the view
// ray, and sample the neon "atmosphere" in the reflection direction. The mesh
// rotation spins the ball; Bloom (DiscoPost) supplies the ray glow. No floor —
// an orb floats. Solid/opaque (unlike the additive volumetric variants).

uniform float uTime;
uniform vec3 uLocalCamPos;
uniform vec3 uColor1; // state primary (crossfaded)
uniform vec3 uColor2; // state secondary (crossfaded)
uniform vec3 uColor3; // static accent
uniform float uFacets; // facet resolution
uniform float uRoughness; // reflection scatter 0..1
uniform float uFractalScale; // env noise scale
uniform float uFractalAmp; // env noise amplitude
uniform float uBgSpeed; // env animation
uniform float uCoreSpeed; // facet-patch animation
uniform float uBrightness;

varying vec3 vLocalPosition;
varying vec3 vNormal;
varying vec3 vViewPosition;

float hash(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.2031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

float smoothNoise(vec2 uv) {
  vec2 cell = floor(uv);
  vec2 local = fract(uv);
  vec2 curve = local * local * (3.0 - 2.0 * local);
  float b00 = hash(cell);
  float b10 = hash(cell + vec2(1.0, 0.0));
  float b01 = hash(cell + vec2(0.0, 1.0));
  float b11 = hash(cell + vec2(1.0, 1.0));
  return mix(mix(b00, b10, curve.x), mix(b01, b11, curve.x), curve.y);
}

float fractalNoise(vec2 uv) {
  float o = 0.0;
  float amp = uFractalAmp;
  mat2 m = mat2(1.6, 1.2, -1.2, 1.7);
  for (int i = 0; i < 5; i++) {
    o += amp * smoothNoise(uv);
    uv = m * uv;
    amp *= 0.4;
  }
  return o;
}

// Procedural neon environment reflected by the facets.
vec3 sampleAtmosphere(vec3 dir, float t) {
  float f1 = fractalNoise(dir.xy * uFractalScale + t * uBgSpeed);
  float f2 = fractalNoise(dir.yz * (uFractalScale + 1.0) - t * (uBgSpeed * 1.5));
  float redGlow = smoothstep(0.85, 1.0, sin(dir.x * 12.0 + f1 * 8.0));
  float cyanGlow = smoothstep(0.90, 1.0, cos(dir.y * 15.0 + f2 * 10.0));
  float purpGlow = smoothstep(0.85, 1.0, sin(dir.z * 10.0 + (f1 + f2) * 4.0));
  vec3 bg = vec3(0.04, 0.015, 0.04);
  bg += uColor1 * 2.0 * redGlow * 1.5;
  bg += uColor2 * 2.0 * cyanGlow * 1.5;
  bg += uColor3 * 2.0 * purpGlow * 1.5;
  return bg;
}

void main() {
  vec3 viewDir = normalize(vLocalPosition - uLocalCamPos);
  vec3 n = normalize(vLocalPosition);

  // Quantise the normal into mirror facets (lat/long tiles).
  float lon = atan(n.z, n.x);
  float lat = acos(clamp(n.y, -1.0, 1.0));
  float res = uFacets;
  float latQ = floor(lat * res) / res;
  float lonRes = max(16.5, floor(res * sin(latQ * 1.11259)));
  float lonQ = floor(lon * lonRes) / lonRes;
  vec3 facetNorm = vec3(sin(latQ) * cos(lonQ), cos(latQ), sin(latQ) * sin(lonQ));

  // Per-facet colour patch + tile-gap mask.
  float patch = fractalNoise(vec2(lonQ, latQ) * 2.2 - uTime * uCoreSpeed);
  vec3 albedo = 0.6 + 0.5 * cos(patch * 4.83 + vec3(1.5, 2.2, 3.2));
  float gu = fract(lon * lonRes);
  float gv = fract(lat * res);
  float mask =
    smoothstep(0.0, 0.1, gu) * smoothstep(1.0, 0.95, gu) *
    smoothstep(0.0, 0.1, gv) * smoothstep(1.0, 0.95, gv);
  albedo *= (1.0 + 0.5 * mask);

  // Reflect the view ray off the facet, optionally scatter, sample the neon env.
  vec3 refl = reflect(viewDir, facetNorm);
  if (uRoughness > 0.001) {
    vec3 jit = normalize(vec3(hash(refl.xy * 7.1), hash(refl.yz * 3.3), hash(refl.zx * 5.7)) - 0.5);
    refl = normalize(mix(refl, jit, uRoughness * 0.5));
  }
  vec3 env = sampleAtmosphere(refl, uTime);

  float fres = pow(max(1.2 - abs(dot(viewDir, facetNorm)), 0.4), 2.0);
  vec3 col = env * albedo * (0.5 + 0.5 * fres) * uBrightness;

  gl_FragColor = vec4(col, 1.0);
}
