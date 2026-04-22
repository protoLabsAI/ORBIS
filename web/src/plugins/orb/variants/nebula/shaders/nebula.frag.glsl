uniform float uTime;
uniform vec3 uLocalCamPos;
uniform vec3 uPrimaryColor;
uniform vec3 uSecondaryColor;
uniform float uDensity;
uniform float uCloudScale;
uniform float uCloudiness;
uniform float uDrift;
uniform float uSoftness;
uniform vec3 uClickDir;
uniform float uClickStrength;

varying vec3 vLocalPosition;
varying vec3 vNormal;
varying vec3 vViewPosition;

// Hash + value noise — cheap and good-looking in motion.
float hash(vec3 p) {
  p = fract(p * 0.3183099 + 0.1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}

float noise(vec3 p) {
  vec3 i = floor(p);
  vec3 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(
      mix(hash(i + vec3(0, 0, 0)), hash(i + vec3(1, 0, 0)), f.x),
      mix(hash(i + vec3(0, 1, 0)), hash(i + vec3(1, 1, 0)), f.x),
      f.y
    ),
    mix(
      mix(hash(i + vec3(0, 0, 1)), hash(i + vec3(1, 0, 1)), f.x),
      mix(hash(i + vec3(0, 1, 1)), hash(i + vec3(1, 1, 1)), f.x),
      f.y
    ),
    f.z
  );
}

// 3-octave FBM — a perceptibly softer but ~45% cheaper than 5.
float fbm(vec3 p) {
  float a = 0.5;
  float s = 0.0;
  for (int i = 0; i < 3; i++) {
    s += a * noise(p);
    p *= 2.02;
    a *= 0.5;
  }
  return s;
}

// Evaluate the nebula density at a point (local sphere space).
float evaluate(vec3 pos) {
  // Drift offset — single axis biased; gives a "flow" direction.
  vec3 drift = vec3(uTime * uDrift * 0.08, uTime * uDrift * -0.05, uTime * uDrift * 0.03);
  // Shape function — single-octave noise is plenty for the "where the clouds
  // are" mask. Formerly a 5-octave FBM call; this one drops a whole FBM per
  // sample.
  float shape = noise(pos * uCloudScale * 0.5 + drift * 0.3);
  // Detail — 3-octave FBM with a single-octave domain warp. The old version
  // ran FBM *inside* FBM (10 octaves per sample); now it's 4 octaves total.
  vec3 warped = pos * uCloudScale + drift + noise(pos * uCloudScale * 1.5) * 0.4;
  float detail = fbm(warped);
  float d = smoothstep(0.25, 0.85, shape) * detail;
  // Edge softness — fade out as we approach the bounding sphere.
  float edge = smoothstep(2.0, 2.0 - uSoftness * 2.0, length(pos));
  return d * edge * uCloudiness;
}

vec2 getVolumeBounds(vec3 origin, vec3 dir, float radius) {
  float b = dot(origin, dir);
  float c = dot(origin, origin) - radius * radius;
  float discriminant = b * b - c;
  if (discriminant < 0.0) return vec2(-1.0);
  float root = sqrt(discriminant);
  return vec2(-b - root, -b + root);
}

// Henyey-Greenstein-ish phase term — cheap forward-scatter boost.
float phase(float cosTheta, float g) {
  float g2 = g * g;
  return (1.0 - g2) / (4.0 * 3.14159 * pow(1.0 + g2 - 2.0 * g * cosTheta, 1.5));
}

vec3 traceVolume(vec3 origin, vec3 dir, vec2 limits) {
  float t = limits.x;
  vec3 accum = vec3(0.0);
  float trans = 1.0;  // transmittance — Beer-Lambert accumulation
  vec3 lightDir = normalize(vec3(0.3, 0.6, 0.5));
  float forward = phase(dot(dir, lightDir), 0.45);
  // Step cap at 28 — with adaptive stepping we rarely need more.
  for (int i = 0; i < 28; i++) {
    if (t > limits.y || trans < 0.02) break;
    vec3 p = origin + t * dir;
    float d = evaluate(p);
    // Adaptive step — stride through empty space, march carefully through
    // dense regions. Halves cost in typical frames.
    float step = mix(0.18, 0.08, smoothstep(0.0, 0.15, d));
    if (d > 0.001) {
      float g = smoothstep(0.0, 0.6, d);
      vec3 baseRamp = mix(uSecondaryColor, uPrimaryColor, g);
      vec3 hotTip = mix(uPrimaryColor, vec3(1.0), 0.35);
      vec3 col = mix(baseRamp, hotTip, smoothstep(0.6, 1.0, d) * 0.5);
      col *= (0.8 + forward * 1.2);
      float sigma = 1.1 * uDensity;
      float absorption = exp(-d * step * sigma);
      accum += trans * (1.0 - absorption) * col;
      trans *= absorption;
    }
    t += step;
  }
  return accum;
}

void main() {
  vec3 rayOrig = uLocalCamPos;
  vec3 rayDir = normalize(vLocalPosition - uLocalCamPos);
  vec2 limits = getVolumeBounds(rayOrig, rayDir, 2.0);
  limits.x = max(limits.x, 0.0);
  if (limits.y < 0.0) discard;

  vec3 volume = traceVolume(rayOrig, rayDir, limits);
  vec3 finalColor = volume;

  vec3 normal = normalize(vNormal);
  vec3 viewDir = normalize(vViewPosition);
  float edgeAA = smoothstep(0.0, 0.05, max(dot(normal, viewDir), 0.0));
  finalColor *= edgeAA;
  float maxLuma = max(finalColor.r, max(finalColor.g, finalColor.b));
  float alpha = clamp(maxLuma * 1.3, 0.0, 1.0) * edgeAA;

  // Click bloom — focused spot on uClickDir.
  vec3 localNormal = normalize(vLocalPosition);
  float clickBoost = smoothstep(0.75, 1.0, dot(localNormal, uClickDir)) * uClickStrength;
  finalColor += uPrimaryColor * clickBoost * 0.7;
  alpha = clamp(alpha + clickBoost * 0.4, 0.0, 1.0);

  gl_FragColor = vec4(finalColor, alpha);
}
