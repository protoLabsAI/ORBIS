// Galaxy particles fragment — soft circular point sprites with
// inverse-radius glow falloff. Discard outside the unit disc so the
// points don't render as squares.

uniform vec3 uColor;
varying float vAlpha;

void main() {
  vec2 uv = gl_PointCoord - vec2(0.5);
  float dist = length(uv);
  if (dist > 0.5) discard;

  float glow = 1.0 - (dist * 2.0);
  glow = pow(glow, 1.8);

  gl_FragColor = vec4(uColor, glow * vAlpha);
}
