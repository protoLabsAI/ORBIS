// Disco ball — a KIFS (kaleidoscopic IFS) fractal raymarched inside the orb's
// hollow glass shell. Adapted from the screen-space lil-gui prototype to the
// shared sphere-mesh ray setup: ray origin = uLocalCamPos, direction = toward
// the fragment's local position; the mesh transform handles rotation, so no
// manual rotation matrix is needed. The orb sphere mesh has radius 2; the KIFS
// pattern domain is scaled up to the prototype's radius-15 space via DOMAIN.

uniform float uTime;
uniform vec3 uLocalCamPos;
uniform vec3 uBaseColor; // primary x colorBoost
uniform vec3 uSecondaryColor; // rim tint
uniform float uBrightness;
uniform float uFoldSteps;
uniform float uKifsScale;
uniform float uKifsOffset;
uniform float uTextureScale;
uniform float uLightSpeed;
uniform float uMaxSteps;
uniform float uAutoRotationSpeed;
uniform float uShellInner; // inner shell radius (outer is 2.0)

varying vec3 vLocalPosition;
varying vec3 vNormal;
varying vec3 vViewPosition;

const float OUTER = 2.0;
const float DOMAIN = 7.5; // radius-2 orb -> radius-15 prototype pattern space

mat2 rot2(float a) {
  float s = sin(a), c = cos(a);
  return mat2(c, -s, s, c);
}

// Ray vs origin-centred sphere; returns near/far t (or -1 on miss).
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

  vec2 hit = intersectSphere(ro, rd, OUTER);
  if (hit.y < 0.0) {
    gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
    return;
  }

  float tStart = max(0.0, hit.x);
  float tEnd = hit.y;
  float stepSize = (tEnd - tStart) / uMaxSteps;
  float t = tStart;

  vec3 acc = vec3(0.0);

  for (int i = 0; i < 220; i++) {
    if (float(i) >= uMaxSteps) break;

    vec3 localPos = ro + rd * t;
    float dist = length(localPos);

    // Hollow shell mask: solid between uShellInner and OUTER.
    float wallMask =
      smoothstep(uShellInner, uShellInner + 0.10, dist) *
      smoothstep(OUTER, OUTER - 0.04, dist);

    if (wallMask > 0.01) {
      vec3 p = localPos * DOMAIN * uTextureScale;

      // Internal "alive" morph (independent of the orb's own spin).
      p.xy *= rot2(uTime * 0.15 * uAutoRotationSpeed);
      p.xz *= rot2(uTime * 0.10 * uAutoRotationSpeed);

      p = abs(p);

      float timeVal = 2.6 + float(i) * 0.356;
      vec3 reflAxis = normalize(tan(timeVal + vec3(2.5, 1.0, 0.0)));
      p = reflect(-p, reflAxis) - vec3(-2.6, -1.0, 0.2);

      float accScale = 0.1;
      if (p.x < p.z) p = p.zyx;

      for (int k = 0; k < 16; k++) {
        if (float(k) >= uFoldSteps) break;
        p *= uKifsScale; accScale *= uKifsScale; p.y += uKifsOffset;
        if (p.y > p.z) p = p.xzy;
        p *= uKifsScale; accScale *= uKifsScale; p.y += uKifsOffset;
        if (p.x < p.y) p = p.yxz;
      }

      float density = max(length(p.xz) / accScale, 0.001);
      vec3 phase = p.xyz;
      vec3 light = exp(sin(uTime * uLightSpeed + phase)) / density;
      acc += uBaseColor * light * stepSize * uBrightness * wallMask;
    }

    t += stepSize;
  }

  gl_FragColor.rgb = tanh(acc);
  gl_FragColor.a = 1.0;

  // Subtle rim to define the glass edge, tinted by the secondary colour.
  float rim = 0.7 - max(dot(-rd, normalize(vLocalPosition)), 0.5);
  gl_FragColor.rgb += uSecondaryColor * 0.5 * pow(max(rim, 0.0), 4.0);
}
