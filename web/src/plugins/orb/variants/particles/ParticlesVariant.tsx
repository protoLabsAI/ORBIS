import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { useOrbState } from '../../useOrbState';
import { useAudioEnvelopes } from '../../shared/hooks/useAudioEnvelopes';
import { useStateCrossfade } from '../../shared/hooks/useStateCrossfade';
import { useIdleBreath } from '../../shared/hooks/useIdleBreath';
import { usePointerInteraction } from '../../shared/hooks/usePointerInteraction';
import { Atmosphere } from '../../shared/atmosphere/Atmosphere';
import { fibonacciSphere } from '../../shared/fibonacciSphere';
import {
  BREATH_AMP,
  MAX_DELTA_S,
  ROT_WRAP,
  ROTATION_SCALE,
} from '../../shared/constants';
import type { ParticlesPreset } from './presets';
import type { VariantProps } from '../registry';

/**
 * Particles variant — Fibonacci-lattice sphere of small icosahedra,
 * each instance jittering/pulsing with the shared audio envelopes.
 *
 * Audio reactivity:
 *   - dBot  → radial push outward (the sphere "breathes out")
 *   - dUser → per-particle jitter (listening agitates)
 *   - state → color crossfade (per-particle HSL variance applied once)
 *   - breath → subtle shell-radius modulation
 */
export function ParticlesVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { gl, camera } = useThree();
  const instRef = useRef<THREE.InstancedMesh>(null);
  const hostRef = useRef<THREE.Group>(null);
  const rayTargetRef = useRef<THREE.Mesh>(null);

  const { params } = useOrbState();
  const base = params as unknown as ParticlesPreset;

  const { dBotRef, dUserRef } = useAudioEnvelopes({ botStream, localStream });
  const { snapRef } = useStateCrossfade(voiceState, base);
  const { breathNormRef } = useIdleBreath();
  const { clickDirRef, clickStrengthRef, dragVelRef } = usePointerInteraction(rayTargetRef);

  const count = Math.max(100, Math.min(3200, Math.round(base.count ?? 1800)));

  // Per-instance base positions (Fibonacci lattice). Recomputed when count changes.
  const basePositions = useMemo(() => fibonacciSphere(count, 1), [count]);

  // Per-instance stable randoms for phase + hue variance. Recomputed on count change.
  const instanceRandoms = useMemo(() => {
    const arr = new Float32Array(count * 3); // [phase, hueOffset, jitterAmp]
    let seed = 0x2fe0c;
    const rnd = () => {
      // xorshift for deterministic per-session randoms.
      seed ^= seed << 13;
      seed ^= seed >>> 17;
      seed ^= seed << 5;
      return ((seed >>> 0) % 10000) / 10000;
    };
    for (let i = 0; i < count; i++) {
      arr[i * 3 + 0] = rnd() * Math.PI * 2;     // phase [0, 2π)
      arr[i * 3 + 1] = rnd() * 2 - 1;           // hueOffset [-1, 1]
      arr[i * 3 + 2] = 0.6 + rnd() * 0.6;       // jitterAmp [0.6, 1.2]
    }
    return arr;
  }, [count]);

  // Tiny icosahedron as the instance shape (12 faces, cheap).
  const geometry = useMemo(() => new THREE.IcosahedronGeometry(1, 0), []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  // Shared material — color applied per-instance via instanceColor.
  const material = useMemo(() => {
    const mat = new THREE.MeshBasicMaterial({
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      toneMapped: false,
    });
    return mat;
  }, []);
  useEffect(() => () => material.dispose(), [material]);

  useEffect(() => {
    gl.setPixelRatio(base.dpr);
  }, [base.dpr, gl]);

  useEffect(() => {
    camera.position.set(0, 0, 6);
  }, [camera]);

  // Invisible raycast target — pointer interaction's pulseAt() raycasts against
  // this sphere since the InstancedMesh doesn't have a smooth surface to hit.
  // Sphere matches the nominal shell radius of the particle cluster.
  const rayGeometry = useMemo(() => new THREE.SphereGeometry(1.2, 24, 24), []);
  useEffect(() => () => rayGeometry.dispose(), [rayGeometry]);

  // Atmosphere shell geometry (separate sphere; matches the shader's normal math).
  const atmosphereGeo = useMemo(() => new THREE.SphereGeometry(1.25, 64, 64), []);
  useEffect(() => () => atmosphereGeo.dispose(), [atmosphereGeo]);

  // Initialize instance colors on palette/state change — called every frame is
  // cheap; per-instance HSL variance is baked from instanceRandoms.
  const scratchColor = useMemo(() => new THREE.Color(), []);
  const scratchMatrix = useMemo(() => new THREE.Matrix4(), []);
  const scratchPos = useMemo(() => new THREE.Vector3(), []);
  const scratchQuat = useMemo(() => new THREE.Quaternion(), []);
  const scratchScale = useMemo(() => new THREE.Vector3(), []);

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const inst = instRef.current;
    const host = hostRef.current;
    const snap = snapRef.current;
    if (!inst || !host || !snap) return;

    const dBot = dBotRef.current;
    const dUser = dUserRef.current;
    const breath = breathNormRef.current;
    const t = performance.now() * 0.001;

    // Shell radius — state scale × breath × radial push on bot voice.
    const shellRadius =
      base.radius *
      snap.scale *
      (1 + breath * BREATH_AMP) *
      (1 + dBot * 0.15 * base.density);

    // Per-instance HSL variance around the state primary color.
    // Spread in degrees → normalized fraction of hue [0, 1].
    const hueSpreadFrac = (base.hueSpread ?? 15) / 360;
    // Sample the HSL of the primary once — reused inside the loop.
    const primHSL = { h: 0, s: 0, l: 0 };
    snap.primary.getHSL(primHSL);

    for (let i = 0; i < count; i++) {
      const bx = basePositions[i].x;
      const by = basePositions[i].y;
      const bz = basePositions[i].z;
      const phase = instanceRandoms[i * 3 + 0];
      const hueOffset = instanceRandoms[i * 3 + 1];
      const jitterAmp = instanceRandoms[i * 3 + 2];

      // Per-instance phase pulse — wave-propagation look rather than
      // unison pumping. Amplitude driven by bot voice.
      const pulse = 1 + Math.sin(t * 1.6 + phase) * 0.04 * (0.6 + dBot * 1.5);

      // User-voice jitter — noise-like offset via stable phase + time.
      const jx = Math.sin(t * 3.1 + phase * 1.3) * dUser * base.jitter * jitterAmp;
      const jy = Math.cos(t * 2.7 + phase * 1.8) * dUser * base.jitter * jitterAmp;
      const jz = Math.sin(t * 2.3 + phase * 0.9) * dUser * base.jitter * jitterAmp;

      scratchPos.set(
        bx * shellRadius * pulse + jx,
        by * shellRadius * pulse + jy,
        bz * shellRadius * pulse + jz,
      );
      scratchQuat.identity();
      scratchScale.setScalar(base.particleSize);
      scratchMatrix.compose(scratchPos, scratchQuat, scratchScale);
      inst.setMatrixAt(i, scratchMatrix);

      // Per-instance color — HSL variance around primary, lerped toward
      // secondary by per-particle hue offset sign.
      const hShifted = (primHSL.h + hueOffset * hueSpreadFrac + 1) % 1;
      scratchColor.setHSL(hShifted, primHSL.s, primHSL.l);
      if (hueOffset > 0.4) scratchColor.lerp(snap.secondary, 0.25 * hueOffset);
      inst.setColorAt(i, scratchColor);
    }

    inst.instanceMatrix.needsUpdate = true;
    if (inst.instanceColor) inst.instanceColor.needsUpdate = true;

    // Host group rotation — auto-rotate + drag momentum.
    const spin = snap.rotation * ROTATION_SCALE * base.speed;
    host.rotation.y += delta * spin + dragVelRef.current.y * delta;
    host.rotation.x += delta * (spin * 0.5) + dragVelRef.current.x * delta;
    if (host.rotation.y > ROT_WRAP)  host.rotation.y -= ROT_WRAP;
    if (host.rotation.y < -ROT_WRAP) host.rotation.y += ROT_WRAP;
    if (host.rotation.x > ROT_WRAP)  host.rotation.x -= ROT_WRAP;
    if (host.rotation.x < -ROT_WRAP) host.rotation.x += ROT_WRAP;
  });

  return (
    <group ref={hostRef}>
      <instancedMesh ref={instRef} args={[geometry, material, count]} frustumCulled={false}>
        {/* instanceColor attribute initialized on first setColorAt call below */}
      </instancedMesh>
      {/* Invisible raycast target so pulse clicks still hit. */}
      <mesh ref={rayTargetRef} geometry={rayGeometry} visible={false} />
      <Atmosphere
        geometry={atmosphereGeo}
        snapRef={snapRef}
        dBotRef={dBotRef}
        dUserRef={dUserRef}
        clickDirRef={clickDirRef}
        clickStrengthRef={clickStrengthRef}
      />
    </group>
  );
}
