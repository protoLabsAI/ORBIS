import * as THREE from 'three';

/**
 * Fibonacci lattice sphere — `n` evenly distributed points on the unit
 * sphere. Prevents the clumping that `Math.random()` inevitably produces.
 *
 * Reference: https://extremelearning.com.au/how-to-evenly-distribute-points-on-a-sphere-more-effectively-than-the-canonical-fibonacci-lattice/
 */
export function fibonacciSphere(n: number, radius = 1): THREE.Vector3[] {
  const goldenAngle = Math.PI * (3 - Math.sqrt(5)); // ≈ 2.39996
  const points: THREE.Vector3[] = new Array(n);
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / Math.max(1, n - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = goldenAngle * i;
    points[i] = new THREE.Vector3(
      Math.cos(theta) * r * radius,
      y * radius,
      Math.sin(theta) * r * radius,
    );
  }
  return points;
}
