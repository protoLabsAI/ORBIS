import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { useFrame } from '@react-three/fiber';
import { AtmosphereMaterial } from './material';
import { useOrbState } from '../../useOrbState';
import type { StateSnapshot } from '../stateSnapshot';

/**
 * Atmosphere shell component. Self-contained — it owns its own mesh,
 * material, and useFrame loop. Variants pass refs for the state
 * snapshot + audio envelopes + click pulse; the atmosphere composes
 * those into its uniforms every frame (glow receives audio pump,
 * color matches state crossfade).
 */
export function Atmosphere({
  geometry,
  snapRef,
  dBotRef,
  dUserRef,
  clickDirRef,
  clickStrengthRef,
}: {
  geometry: THREE.BufferGeometry;
  snapRef: React.RefObject<StateSnapshot>;
  dBotRef: React.RefObject<number>;
  dUserRef: React.RefObject<number>;
  clickDirRef: React.RefObject<THREE.Vector3>;
  clickStrengthRef: React.RefObject<number>;
}) {
  const meshRef = useRef<THREE.Mesh>(null);
  const matRef = useRef<InstanceType<typeof AtmosphereMaterial>>(null);
  const { params } = useOrbState();
  const level = Number(params.atmosphereLevel ?? 1.0);
  const scale = Number(params.atmosphereScale ?? 1.03);

  useEffect(() => {
    if (!matRef.current) return;
    matRef.current.uniforms.uLevel.value = level;
  }, [level]);

  useEffect(() => {
    if (!meshRef.current) return;
    meshRef.current.scale.setScalar(scale);
  }, [scale]);

  useFrame(() => {
    const m = matRef.current;
    const snap = snapRef.current;
    if (!m || !snap) return;
    const dBot = dBotRef.current ?? 0;
    const dUser = dUserRef.current ?? 0;
    m.uniforms.uColor.value.copy(snap.primary);
    m.uniforms.uColorSecondary.value.copy(snap.secondary);
    m.uniforms.uGlow.value = snap.glow + dBot * 1.1 + dUser * 0.35;
    m.uniforms.uClickDir.value.copy(clickDirRef.current ?? new THREE.Vector3(0, 0, 1));
    m.uniforms.uClickStrength.value = clickStrengthRef.current ?? 0;
  });

  return (
    <mesh ref={meshRef} geometry={geometry} scale={scale}>
      <atmosphereMaterial
        ref={matRef}
        transparent
        side={THREE.FrontSide}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
        attach="material"
      />
    </mesh>
  );
}
