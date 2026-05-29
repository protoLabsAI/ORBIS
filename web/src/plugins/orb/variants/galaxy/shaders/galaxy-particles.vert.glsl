// Galaxy particles vertex — point sprites floating inside the plasma
// sphere. Subtle sin/cos drift on each particle's position; size
// scales with attribute aSize and inverse view-space depth.

uniform float uTime;
attribute float aSize;
varying float vAlpha;

void main() {
  vec3 pos = position;

  // Subtle floating drift — the particle field lives, doesn't lock
  // to the rotating plasma group. Amplitudes deliberately small so
  // the drift reads as ambient motion, not a swirl.
  pos.y += sin(uTime * 0.2 + pos.x) * 0.02;
  pos.x += cos(uTime * 0.15 + pos.z) * 0.02;

  vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
  gl_Position = projectionMatrix * mvPosition;

  // Per-particle base size + perspective scaling. Large particles
  // stay readable when zoomed out; small particles fade into the
  // plasma when close.
  float baseSize = 8.0 * aSize + 4.0;
  gl_PointSize = baseSize * (1.0 / -mvPosition.z);

  // Twinkle — small alpha oscillation per particle so the field
  // doesn't look static.
  vAlpha = 0.8 + 0.2 * sin(uTime + aSize * 10.0);
}
