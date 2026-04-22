varying vec3 vNormal;
varying vec3 vViewPosition;
varying vec3 vLocalPos;

void main() {
  vNormal = normalize(normalMatrix * normal);
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  vViewPosition = -mvPosition.xyz;
  vLocalPos = position;
  gl_Position = projectionMatrix * mvPosition;
}
