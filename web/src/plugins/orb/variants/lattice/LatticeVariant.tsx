import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { LatticeMaterial } from './materials';
import type { LatticePreset } from './presets';
import { useAudioEnvelopes } from '../../shared/hooks/useAudioEnvelopes';
import { useStateCrossfade } from '../../shared/hooks/useStateCrossfade';
import { useIdleBreath } from '../../shared/hooks/useIdleBreath';
import { usePointerInteraction } from '../../shared/hooks/usePointerInteraction';
import { useComposedBase } from '../../shared/hooks/useComposedBase';
import {
  BREATH_AMP,
  MAX_DELTA_S,
  ROT_WRAP,
  ROTATION_SCALE,
  TIME_WRAP,
} from '../../shared/constants';
import type { VariantProps } from '../registry';

/**
 * Lattice variant — sphere-mounted port of an AABB-bounded volumetric
 * raymarch through a wrapped-grid SDF. The cube IS the silhouette;
 * lattice cell-walls trace inside it as faceted shells.
 *
 * Distinct from the other volumetric variants:
 *   - tetra:   tetrahedron + spherical inversion (organic, curling)
 *   - fractal: organic decay-density (smoke-like)
 *   - nebula:  FBM cloud noise (soft)
 *   - lattice: AABB cube + repeating grid (crystalline, faceted)
 *
 * Audio reactivity:
 *   - dBot  → glow boost (orb lights up while speaking)
 *   - dUser → distortion bump (lattice wobbles during listen)
 *   - state → primary/secondary crossfade through shared snapshot
 *   - breath → mesh scale modulation
 */
export function LatticeVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { camera, gl } = useThree();
  const meshRef = useRef<THREE.Mesh>(null);
  const matRef = useRef<InstanceType<typeof LatticeMaterial>>(null);

  const { base, effectiveState } = useComposedBase<LatticePreset>(voiceState);
  const baseRef = useRef(base);
  baseRef.current = base;

  const { dBotRef, dUserRef } = useAudioEnvelopes({ botStream, localStream });
  const { snapRef } = useStateCrossfade(effectiveState, base);
  const { breathNormRef } = useIdleBreath();
  const { clickDirRef, clickStrengthRef, dragVelRef } = usePointerInteraction(meshRef);

  // Direct (non-state-driven) uniforms — applied on change.
  useEffect(() => {
    const m = matRef.current;
    if (!m) return;
    m.uniforms.uCubeSize.value  = base.cubeSize;
    m.uniforms.uGridScale.value = base.gridScale;
    m.uniforms.uColorOffset.value.set(
      base.colorOffsetR,
      base.colorOffsetG,
      base.colorOffsetB,
    );
  }, [
    base.cubeSize, base.gridScale,
    base.colorOffsetR, base.colorOffsetG, base.colorOffsetB,
  ]);

  useEffect(() => {
    gl.setPixelRatio(base.dpr);
  }, [base.dpr, gl]);

  useEffect(() => {
    camera.position.set(0, 0, 13);
  }, [camera]);

  const scratchCam = useMemo(() => new THREE.Vector3(), []);
  // Sphere mesh sized to comfortably contain the cube at any cubeSize
  // (max=4 → diagonal ~6.93). Radius 5 → no edge clipping. Mesh is
  // invisible; alpha falls off where the AABB SDF carves nothing.
  const geometry = useMemo(() => new THREE.SphereGeometry(5.0, 32, 32), []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const m = matRef.current;
    const mesh = meshRef.current;
    const snap = snapRef.current;
    if (!m || !mesh || !snap) return;

    const dBot = dBotRef.current;
    const dUser = dUserRef.current;

    // Glow rides the volume accumulator; bot energy bumps it on top.
    m.uniforms.uGlow.value = base.glow * (0.85 + snap.density * 0.25) + dBot * 0.18;

    // Distortion: base + user energy. Capped low so the cube
    // silhouette doesn't visibly wobble during normal speech.
    m.uniforms.uDistortion.value = base.distortion + dUser * 0.10;

    m.uniforms.uPrimaryColor.value.copy(snap.primary);
    m.uniforms.uSecondaryColor.value.copy(snap.secondary);
    m.uniforms.uClickDir.value.copy(clickDirRef.current);
    m.uniforms.uClickStrength.value = clickStrengthRef.current;

    // Scale: state × breath × audio pump.
    const scale = snap.scale * (1 + breathNormRef.current * BREATH_AMP) * (1 + dBot * 0.06);
    mesh.scale.setScalar(scale);

    // Time + rotation.
    m.uniforms.uTime.value += delta * snap.speed;
    mesh.rotation.y += delta * snap.rotation * ROTATION_SCALE + dragVelRef.current.y * delta;
    mesh.rotation.x += delta * (snap.rotation * 0.5) * ROTATION_SCALE + dragVelRef.current.x * delta;

    // Float32 wrap.
    if (m.uniforms.uTime.value > TIME_WRAP) m.uniforms.uTime.value -= TIME_WRAP;
    if (mesh.rotation.y > ROT_WRAP)  mesh.rotation.y -= ROT_WRAP;
    if (mesh.rotation.y < -ROT_WRAP) mesh.rotation.y += ROT_WRAP;
    if (mesh.rotation.x > ROT_WRAP)  mesh.rotation.x -= ROT_WRAP;
    if (mesh.rotation.x < -ROT_WRAP) mesh.rotation.x += ROT_WRAP;

    // Local camera position for the raymarch ray origin.
    mesh.updateMatrixWorld();
    scratchCam.copy(camera.position);
    mesh.worldToLocal(scratchCam);
    m.uniforms.uLocalCamPos.value.copy(scratchCam);
  });

  return (
    <mesh ref={meshRef} geometry={geometry}>
      <latticeMaterial
        ref={matRef}
        transparent
        side={THREE.DoubleSide}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
        attach="material"
      />
    </mesh>
  );
}
