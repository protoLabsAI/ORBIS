uniform float uTime;
uniform vec3 uLocalCamPos;
uniform vec3 uPrimaryColor;
uniform vec3 uSecondaryColor;
uniform float uDensity;
uniform float uFractalIters;
uniform float uFractalScale;
uniform float uFractalDecay;
uniform float uInternalAnim;
uniform float uSmoothness;
uniform float uAsymmetry;
uniform float uAtmosphereGlow;
uniform vec3 uClickDir;
uniform float uClickStrength;

varying vec3 vLocalPosition;
varying vec3 vNormal;
varying vec3 vViewPosition;

float evaluateStructure(vec3 pos) {
  float densityAcc = 0.0;
  vec3 anchor = pos;
  float animTime = uTime * uInternalAnim;
  float s = sin(animTime);
  float c = cos(animTime);
  mat2 rotAnim = mat2(c, s, -s, c);
  float a = 0.5 * uAsymmetry;
  mat2 rotAsym1 = mat2(cos(a), sin(a), -sin(a), cos(a));
  float b = 0.3 * uAsymmetry;
  mat2 rotAsym2 = mat2(cos(b), sin(b), -sin(b), cos(b));
  for (int step = 0; step < 12; ++step) {
    if (float(step) >= uFractalIters) break;
    pos.xy *= rotAnim;
    pos.yz *= rotAnim;
    pos.xz *= rotAsym1;
    pos.yz *= rotAsym2;
    pos += vec3(0.05, -0.02, 0.03) * uAsymmetry;
    vec3 foldedPos = sqrt(pos * pos + uSmoothness);
    float magnitudeSq = dot(foldedPos, foldedPos);
    magnitudeSq = max(magnitudeSq, 0.00001);
    pos = (uFractalScale * foldedPos / magnitudeSq) - uFractalScale;
    float ySq = pos.y * pos.y;
    float zSq = pos.z * pos.z;
    float yz2 = 2.0 * pos.y * pos.z;
    pos.yz = vec2(ySq - zSq, yz2);
    pos = vec3(pos.z, pos.x, pos.y);
    densityAcc += exp(uFractalDecay * abs(dot(pos, anchor)));
  }
  return densityAcc * 0.5;
}

vec2 getVolumeBounds(vec3 origin, vec3 dir, float radius) {
  float b = dot(origin, dir);
  float c = dot(origin, origin) - radius * radius;
  float discriminant = b * b - c;
  if (discriminant < 0.0) return vec2(-1.0);
  float root = sqrt(discriminant);
  return vec2(-b - root, -b + root);
}

vec3 traceEnergy(vec3 origin, vec3 dir, vec2 limits) {
  float currentDepth = limits.x;
  float marchStep = 0.02;
  vec3 finalEnergy = vec3(0.0);
  float fieldVal = 0.0;
  for (int i = 0; i < 64; i++) {
    currentDepth += marchStep * exp(-2.0 * fieldVal);
    if (currentDepth > limits.y) break;
    vec3 samplePoint = origin + currentDepth * dir;
    fieldVal = evaluateStructure(samplePoint);
    float vSq = fieldVal * fieldVal;
    // Single clean secondary→primary ramp, then a white-tipped highlight
    // (capped at 30 %) for the densest samples. No additive overshoot.
    float g   = smoothstep(0.05, 0.75, fieldVal);
    float hot = smoothstep(0.70, 1.00, fieldVal);
    vec3 baseGradient = mix(uSecondaryColor, uPrimaryColor, g);
    vec3 hotColor = mix(uPrimaryColor, vec3(1.0), 0.30);
    vec3 currentGradient = mix(baseGradient, hotColor, hot * 0.55);
    vec3 emission = currentGradient * (fieldVal * 1.8 + vSq * 1.0);
    finalEnergy = 0.99 * finalEnergy + (0.08 * uDensity) * emission;
  }
  return finalEnergy;
}

void main() {
  vec3 rayOrig = uLocalCamPos;
  vec3 rayDir = normalize(vLocalPosition - uLocalCamPos);
  float t = uTime * 0.1;
  float s = sin(t);
  float c = cos(t);
  mat2 rotXZ = mat2(c, s, -s, c);
  rayOrig.xz *= rotXZ;
  rayDir.xz *= rotXZ;
  vec2 limits = getVolumeBounds(rayOrig, rayDir, 2.0);
  // When camera is inside the sphere, the near-intersection sits behind
  // us (limits.x < 0). Clamp start to camera origin (0) so the ray marches
  // from the camera outward to the far side, not from behind.
  limits.x = max(limits.x, 0.0);
  if (limits.y < 0.0) discard;
  vec3 volumeColor = traceEnergy(rayOrig, rayDir, limits);
  vec3 normal = normalize(vNormal);
  vec3 viewDir = normalize(vViewPosition);
  float facingRatio = max(dot(normal, viewDir), 0.0);
  float edgeAA = smoothstep(0.0, 0.05, facingRatio);
  vec3 finalColor = 0.5 * log(1.0 + volumeColor);
  finalColor = clamp(finalColor, 0.0, 1.0);
  finalColor *= edgeAA;
  float maxLuma = max(finalColor.r, max(finalColor.g, finalColor.b));
  float alpha = clamp(maxLuma * 1.5, 0.0, 1.0) * edgeAA;

  // Click bloom — tight spot centered on uClickDir. Raising the
  // smoothstep lower bound from 0.25 → 0.75 narrows the lit cone
  // from ~76° to ~41°, so the bloom reads as a focused touch rather
  // than a whole-hemisphere glow.
  vec3 localNormal = normalize(vLocalPosition);
  float clickBoost = smoothstep(0.75, 1.0, dot(localNormal, uClickDir)) * uClickStrength;
  finalColor += uPrimaryColor * clickBoost * 0.9;
  alpha = clamp(alpha + clickBoost * 0.4, 0.0, 1.0);

  gl_FragColor = vec4(finalColor, alpha);
}
