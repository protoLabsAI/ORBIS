// Galaxy plasma fragment — domain-warped 3D Simplex / FBM noise
// produces volumetric-looking gas inside the orb. Three-stop colour
// palette (deep / mid / bright) blends into a white-tipped highlight
// at the densest samples. Density-based alpha + facing-ratio depth
// modulation make the plasma read as "looking into" a sphere of gas
// rather than a flat decal.
//
// Uses sphere.vert.glsl's vLocalPosition / vNormal / vViewPosition.
// The noise functions are inlined here rather than in a shared file
// because no other variant currently needs 3D Simplex; if a third
// variant wants it, lift to shared/shaders/snoise3d.glsl.inc.

uniform float uTime;
uniform float uScale;
uniform float uBrightness;
uniform float uThreshold;
uniform vec3  uColorDeep;
uniform vec3  uColorMid;
uniform vec3  uColorBright;
uniform vec3  uPrimaryColor;
uniform vec3  uSecondaryColor;
uniform float uVoiceMix;       // 0 = procedural palette only, 1 = full voice tint

uniform vec3  uClickDir;
uniform float uClickStrength;

varying vec3 vLocalPosition;
varying vec3 vNormal;
varying vec3 vViewPosition;

// 3D Simplex noise (Ashima Arts / Stefan Gustavson). Standard
// reference implementation; output range roughly [-1, +1].
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) {
  const vec2 C = vec2(1.0/6.0, 1.0/3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i  = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g  = step(x0.yzx, x0.xyz);
  vec3 l  = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(
        i.z + vec4(0.0, i1.z, i2.z, 1.0))
      + i.y + vec4(0.0, i1.y, i2.y, 1.0))
      + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x  = x_ * ns.x + ns.yyyy;
  vec4 y  = y_ * ns.x + ns.yyyy;
  vec4 h  = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}

// Three-octave fractional Brownian motion. Bounded to 3 octaves so
// the per-pixel cost stays predictable; bumping to 5+ would crank
// detail at the cost of frame-rate.
float fbm(vec3 p) {
  float total = 0.0;
  float amplitude = 0.5;
  float frequency = 1.0;
  for (int i = 0; i < 3; i++) {
    total += snoise(p * frequency) * amplitude;
    amplitude *= 0.5;
    frequency *= 2.0;
  }
  return total;
}

void main() {
  vec3 p = vLocalPosition * uScale;

  // Domain-warp via 3 fbm samples — the classic "ghost in the
  // machine" pattern that gives the plasma its swirly, nebula-ish feel.
  vec3 q = vec3(
    fbm(p + vec3(0.0, uTime * 0.05, 0.0)),
    fbm(p + vec3(5.2, 1.3, 2.8) + uTime * 0.05),
    fbm(p + vec3(2.2, 8.4, 0.5) - uTime * 0.02)
  );

  float density = fbm(p + 2.0 * q);
  float t = (density + 0.4) * 0.8;
  float alpha = smoothstep(uThreshold, 0.7, t);

  // Three-stop palette + white-tip highlight at the densest samples.
  vec3 cWhite = vec3(1.0, 1.0, 1.0);
  vec3 color = mix(uColorDeep, uColorMid, smoothstep(uThreshold, 0.5, t));
  color = mix(color, uColorBright, smoothstep(0.5, 0.8, t));
  color = mix(color, cWhite, smoothstep(0.8, 1.0, t));

  // Voice tint — primary takes the bright cores, secondary tugs the
  // mid layer. uVoiceMix controls how strongly the orb's voice-state
  // colours bleed through; 0 keeps the procedural palette pristine.
  vec3 voicePalette = mix(uColorMid, uColorBright, smoothstep(0.5, 0.8, t));
  voicePalette = mix(voicePalette, uPrimaryColor, smoothstep(0.5, 1.0, t));
  voicePalette = mix(uSecondaryColor, voicePalette, smoothstep(0.0, 0.5, t));
  color = mix(color, voicePalette, uVoiceMix);

  // Facing-ratio depth modulation — plasma at the silhouette is
  // dimmer than plasma facing the camera. Reads as "looking into" a
  // sphere of gas rather than a flat decal.
  float facing = dot(normalize(vNormal), normalize(vViewPosition));
  float depthFactor = (facing + 1.0) * 0.5;
  float finalAlpha = alpha * (0.02 + 0.98 * depthFactor);

  // Click reactivity — surface-luminance boost on the side facing
  // the click. Same shape as the volumetric variants.
  vec3 hitDir = normalize(vLocalPosition);
  float clickBoost = uClickStrength * smoothstep(0.55, 1.0, dot(hitDir, uClickDir));
  color += uPrimaryColor * clickBoost * 0.4;

  gl_FragColor = vec4(color * uBrightness, finalAlpha);
}
