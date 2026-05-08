// Liquid variant — sphere-mounted port of a hard-surface raymarched
// orb whose surface is displaced by a domain-warped value-noise
// height field. Distinct from the other variants: every other one
// is volumetric (additive accumulator); this one finds a single
// surface hit, computes a normal, and shades with diffuse + fill +
// specular + rim — mercury / oil-puddle aesthetic.
//
// Adapted from a shadertoy-style sketch (full-screen quad + ortho
// camera + screen-space rotation matrix). Same playbook as the
// volumetric ports: ray origin = uLocalCamPos, ray dir = normalize
// (vLocalPosition - uLocalCamPos). The original transformed lights
// through the same rotation matrix as the camera (so lights track
// the view); on a sphere-mounted mesh we keep light directions in
// object space, which means lighting visually rotates with the orb
// — close enough to the original feel without rebuilding the
// camera-locked lighting setup.
//
// Texture-load + image-mapping path from the source is dropped
// here — it's an authoring affordance with no fit in the variant
// pattern (no upload UI), and the procedural 4-colour palette gives
// the variant its identity on its own.

uniform float uTime;
uniform vec3  uLocalCamPos;
uniform vec3  uPrimaryColor;
uniform vec3  uSecondaryColor;

uniform float uSphereSize;       // base orb radius
uniform float uWarpAmp;          // domain-warp amplitude per iteration
uniform float uWarpFalloff;      // amplitude decay across warp iterations
uniform float uWarpStartFreq;    // starting frequency
uniform float uWarpSteps;        // 1..20 — more steps = more chaotic surface
uniform float uWarpVelocity;     // animates the warp phase
uniform float uNoiseContrast;    // pow() exponent on the noise field
uniform float uHeightAmp;        // height-field displacement on the sphere

uniform vec3  uColor1;           // 4-colour procedural palette stops
uniform vec3  uColor2;
uniform vec3  uColor3;
uniform vec3  uColor4;

uniform vec3  uLightDir;         // main light (object-space)
uniform vec3  uFillDir;          // fill light (object-space)
uniform float uAmbient;
uniform float uDiffuse;
uniform float uFillLight;
uniform float uSpecularPower;
uniform float uSpecularIntensity;

uniform vec3  uClickDir;
uniform float uClickStrength;

varying vec3 vLocalPosition;

#define MAX_STEPS 96
#define SURF_DIST 0.001
#define MAX_DIST 14.0
#define PI 3.14159265359

// 3D pseudo-random number generator.
float prng3D(vec3 p) {
  return fract(sin(dot(p, vec3(12.9898, 78.283, 37.919))) * 43758.7053);
}

// 3D value noise via tri-cubic interpolation. Output is in [0, 1]
// before the contrast power; raised to uNoiseContrast for shaping.
float valNoise3D(vec3 p) {
  vec3 i = floor(p);
  vec3 f = fract(p);
  vec3 u = f * f * (2.4 - 1.4 * f);

  float n000 = prng3D(i + vec3(0.0, 0.0, 0.0));
  float n100 = prng3D(i + vec3(1.0, 0.0, 0.0));
  float n010 = prng3D(i + vec3(0.0, 1.0, 0.0));
  float n110 = prng3D(i + vec3(1.0, 1.0, 0.0));
  float n001 = prng3D(i + vec3(0.0, 0.0, 1.0));
  float n101 = prng3D(i + vec3(1.0, 0.0, 1.0));
  float n011 = prng3D(i + vec3(0.0, 1.0, 1.0));
  float n111 = prng3D(i + vec3(1.0, 1.0, 1.0));

  float nx00 = mix(n000, n100, u.x);
  float nx10 = mix(n010, n110, u.x);
  float nx01 = mix(n001, n101, u.x);
  float nx11 = mix(n011, n111, u.x);
  float nxy0 = mix(nx00, nx10, u.y);
  float nxy1 = mix(nx01, nx11, u.y);

  return pow(mix(nxy0, nxy1, u.z), uNoiseContrast);
}

// Domain warp — chains uWarpSteps sine-wave displacements with
// per-iteration frequency / amplitude shifts. Verbatim from the
// source minus the shader-side rotation (R3F handles that).
vec3 warpPosition(vec3 pos) {
  pos *= 0.4;
  float currentFreq = uWarpStartFreq;
  float pwr = pow(uWarpFalloff, uWarpStartFreq);
  float basePhase = uTime * uWarpVelocity;

  for (float iter = 0.0; iter < 20.0; iter++) {
    if (iter >= uWarpSteps) break;
    float phaseX = basePhase + currentFreq * 0.18;
    float phaseY = basePhase + currentFreq * 0.21;
    float phaseZ = basePhase + currentFreq * 0.24;

    vec3 offset;
    offset.x = uWarpAmp * sin(pos.y * pwr + phaseX) / pwr;
    offset.y = uWarpAmp * sin(pos.z * pwr + phaseY) / pwr;
    offset.z = uWarpAmp * sin(pos.x * pwr + phaseZ) / pwr;
    pos += offset;

    currentFreq += 1.0;
    pwr *= uWarpFalloff;
  }
  return pos;
}

float getHeightField(vec3 warpedPos) {
  return valNoise3D(warpedPos * 3.1 + uTime * 0.3);
}

float getChromaShift(vec3 warpedPos) {
  return valNoise3D(warpedPos * 1.5 + uTime * 0.6 + 20.0);
}

// Procedural 4-colour palette — picks an interval based on a hash
// of position + chroma offset, then smoothsteps between adjacent
// stops. Wraps cleanly because stage 4 mixes back to color1.
vec3 dynamicIrradiance(vec3 dir, float offset) {
  float t = fract((dir.x + dir.y + dir.z) * 0.27 + pow(offset, 4.6));
  t *= 4.0;
  float stage = floor(t);
  float blend = smoothstep(0.0, 1.4, fract(t));

  if (stage == 0.0) return mix(uColor1, uColor2, blend);
  if (stage == 1.0) return mix(uColor2, uColor3, blend);
  if (stage == 2.0) return mix(uColor3, uColor4, blend);
  return mix(uColor4, uColor1, blend);
}

// SDF: sphere of uSphereSize radius, displaced outward by the
// height field. The 0.35 multiplier on the SDF keeps the marcher
// from overstepping the displaced surface on each iteration.
float mapGeometry(vec3 p) {
  float radius = length(p);
  if (radius < 1e-4) return -uSphereSize;
  vec3 dir = p / radius;
  vec3 warped = warpPosition(dir);
  float heightNoise = getHeightField(warped);
  float heightMap = smoothstep(0.2, 0.8, heightNoise);
  float surfaceDist = radius - uSphereSize - heightMap * uHeightAmp;
  return surfaceDist * 0.35;
}

vec3 calculateNormal(vec3 p) {
  vec2 e = vec2(0.01, 0.0);
  vec3 n = mapGeometry(p) - vec3(
    mapGeometry(p - e.xyy),
    mapGeometry(p - e.yxy),
    mapGeometry(p - e.yyx)
  );
  return normalize(n);
}

void main() {
  vec3 ro = uLocalCamPos;
  vec3 rd = normalize(vLocalPosition - uLocalCamPos);

  float distTraveled = 0.0;
  vec3 hitPos = ro;
  bool isHit = false;

  for (int i = 0; i < MAX_STEPS; i++) {
    hitPos = ro + rd * distTraveled;
    float d = mapGeometry(hitPos);
    if (abs(d) < SURF_DIST) {
      isHit = true;
      break;
    }
    if (distTraveled > MAX_DIST) break;
    distTraveled += d;
  }

  if (!isHit) discard;

  vec3 normal = calculateNormal(hitPos);

  // Surface-color sampling — same warp + chroma noise, used as
  // input to the procedural palette.
  vec3 dir = normalize(hitPos);
  vec3 warped = warpPosition(dir);
  float heightNoise = getHeightField(warped);
  float chromaShift = getChromaShift(warped);

  vec3 albedo = dynamicIrradiance(dir, chromaShift);

  // Voice-state tint: 25 % primary↔secondary mix lightly tugs the
  // procedural palette toward the orb's voice colours without
  // overwhelming the variant identity.
  vec3 voiceTint = mix(uSecondaryColor, uPrimaryColor, 0.5);
  albedo = mix(albedo, albedo * voiceTint * 1.5, 0.25);

  float patternContrast = smoothstep(0.4, 0.8, heightNoise);
  vec3 surfaceColor = (0.4 + patternContrast * 1.3) * albedo;

  // Lights kept in object space — they appear to track the orb
  // surface as the mesh rotates; close to the original camera-locked
  // shading without rebuilding the source's transform-everything-
  // by-rotation pipeline.
  vec3 lightDir = normalize(uLightDir);
  vec3 fillDir  = normalize(uFillDir);
  vec3 viewDir  = -rd;

  float diffuseHit = max(dot(normal, lightDir), -0.8);
  float fillHit    = max(dot(normal, fillDir), 0.0);
  float diffuse    = diffuseHit * uDiffuse + fillHit * uFillLight;
  vec3 finalColor = surfaceColor * (diffuse + uAmbient);

  vec3 halfVector = normalize(lightDir + viewDir);
  float specular = pow(max(dot(normal, halfVector), 0.0), uSpecularPower);
  finalColor += vec3(1.0, 0.9, 0.8) * specular * uSpecularIntensity * patternContrast;

  // Rim — the wet/specular edge that gives the variant its
  // "liquid" feel.
  float rim = 1.2 - max(dot(normal, viewDir), 0.4);
  rim = smoothstep(0.6, 1.0, rim);
  finalColor += albedo * rim * 0.5;

  // Click reactivity — boost surface luminance on the side facing
  // the click direction. Same shape as the volumetric variants but
  // applied at the surface instead of inside the march.
  vec3 hitDir = normalize(hitPos);
  float clickBoost = uClickStrength * smoothstep(0.55, 1.0, dot(hitDir, uClickDir));
  finalColor += uPrimaryColor * clickBoost * 0.4;

  finalColor = clamp(finalColor, 0.0, 1.0);
  gl_FragColor = vec4(finalColor, 1.0);
}
