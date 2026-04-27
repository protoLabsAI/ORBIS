import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { useFrame } from '@react-three/fiber';
import { AtmosphereMaterial } from './material';
import type { StateSnapshot } from '../stateSnapshot';

export function Atmosphere({
  geometry,
  snapRef,
  dBotRef,
  dUserRef,
  clickDirRef,
  clickStrengthRef,
  atmosphereLevel = 1.0,
  atmosphereScale = 1.03,
}: {
  geometry: THREE.BufferGeometry;
  snapRef: React.RefObject<StateSnapshot>;
  dBotRef: React.RefObject<number>;
  dUserRef: React.RefObject<number>;
  clickDirRef: React.RefObject<THREE.Vector3>;
  clickStrengthRef: React.RefObject<number>;
  atmosphereLevel?: number;
  atmosphereScale?: number;
}) {
  const meshRef = useRef<THREE.Mesh>(null);
  const matRef = useRef<InstanceType<typeof AtmosphereMaterial>>(null);

  useEffect(() => {
    if (!matRef.current) return;
    matRef.current.uniforms.uLevel.value = atmosphereLevel;
  }, [atmosphereLevel]);

  useEffect(() => {
    if (!meshRef.current) return;
    meshRef.current.scale.setScalar(atmosphereScale);
  }, [atmosphereScale]);

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
    <mesh ref={meshRef} geometry={geometry} scale={atmosphereScale}>
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
