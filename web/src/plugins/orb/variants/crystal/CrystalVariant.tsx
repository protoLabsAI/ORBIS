import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { Environment, MeshTransmissionMaterial } from '@react-three/drei';
import type { CrystalPreset } from './presets';
import { useOrbState } from '../../useOrbState';
import { useAudioEnvelopes } from '../../shared/hooks/useAudioEnvelopes';
import { useStateCrossfade } from '../../shared/hooks/useStateCrossfade';
import { useIdleBreath } from '../../shared/hooks/useIdleBreath';
import { usePointerInteraction } from '../../shared/hooks/usePointerInteraction';
import { Atmosphere } from '../../shared/atmosphere/Atmosphere';
import {
  BREATH_AMP,
  MAX_DELTA_S,
  ROT_WRAP,
  ROTATION_SCALE,
} from '../../shared/constants';
import type { VariantProps } from '../registry';

/**
 * Crystal variant — faceted icosahedron with PBR transmission + HDRI.
 * drei's MeshTransmissionMaterial handles iridescence, chromatic
 * aberration, and anisotropic blur; Environment supplies the reflection
 * map that sells the "this is a real crystal" reading.
 *
 * Audio reactivity:
 *   - dBot  → emissive pulse + scale wobble (the crystal breathes out)
 *   - dUser → distortion amplitude (listening agitates the surface)
 *   - state → color crossfade through the shared snapshot
 *   - breath → gentle scale modulation at ~0.1 Hz
 */
export function CrystalVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { camera, gl } = useThree();
  const meshRef = useRef<THREE.Mesh>(null);
  // drei's MeshTransmissionMaterial is typed loosely; use `any` for the mat ref.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const matRef = useRef<any>(null);

  const { params } = useOrbState();
  const base = params as unknown as CrystalPreset;

  const { dBotRef, dUserRef } = useAudioEnvelopes({ botStream, localStream });
  const { snapRef } = useStateCrossfade(voiceState, base);
  const { breathNormRef } = useIdleBreath();
  const { clickDirRef, clickStrengthRef, dragVelRef } = usePointerInteraction(meshRef);

  // Icosahedron geometry — regenerated when detail level changes.
  const geometry = useMemo(
    () => new THREE.IcosahedronGeometry(1.2, Math.max(0, Math.min(3, Math.round(base.detail)))),
    [base.detail],
  );
  useEffect(() => () => geometry.dispose(), [geometry]);

  useEffect(() => {
    gl.setPixelRatio(base.dpr);
  }, [base.dpr, gl]);

  useEffect(() => {
    camera.position.set(0, 0, 6);
  }, [camera]);

  // Atmosphere mesh needs its own geometry (sphere — matches the shader's
  // spherical normal math). Crystal geometry is icosahedral; atmosphere stays spherical.
  const atmosphereGeo = useMemo(() => new THREE.SphereGeometry(1.25, 64, 64), []);
  useEffect(() => () => atmosphereGeo.dispose(), [atmosphereGeo]);

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const mesh = meshRef.current;
    const mat = matRef.current;
    const snap = snapRef.current;
    if (!mesh || !snap) return;

    const dBot = dBotRef.current;
    const dUser = dUserRef.current;

    // Color the crystal via attenuation (shadow tint) + emissive.
    if (mat) {
      // Attenuation color picks up the primary at low depth, secondary deep.
      if (mat.attenuationColor?.copy) mat.attenuationColor.copy(snap.primary);
      if (mat.color?.copy) mat.color.copy(snap.secondary);
      // Distortion amplitude pumps on user voice (listening agitates surface).
      if ('distortion' in mat) mat.distortion = base.asymmetry * (0.15 + dUser * 0.6);
      if ('temporalDistortion' in mat) mat.temporalDistortion = 0.1 + dBot * 0.25;
      // Emissive pulse on bot speaking. MeshTransmissionMaterial exposes
      // emissive through its MeshPhysicalMaterial base.
      if (mat.emissive?.copy) mat.emissive.copy(snap.primary);
      if ('emissiveIntensity' in mat) mat.emissiveIntensity = snap.glow * 0.6 + dBot * 0.9;
    }

    // Scale: state × breath × audio wobble (using base.density as amplitude).
    const wobble = 1 + dBot * 0.12 * base.density + breathNormRef.current * BREATH_AMP;
    mesh.scale.setScalar(snap.scale * wobble);

    // Rotation: state × ROTATION_SCALE × speed + drag momentum.
    // dUser adds a subtle "shimmer" spin when the user speaks.
    const spin = snap.rotation * ROTATION_SCALE * base.speed + dUser * 0.15;
    mesh.rotation.y += delta * spin + dragVelRef.current.y * delta;
    mesh.rotation.x += delta * (spin * 0.5) + dragVelRef.current.x * delta;

    if (mesh.rotation.y > ROT_WRAP)  mesh.rotation.y -= ROT_WRAP;
    if (mesh.rotation.y < -ROT_WRAP) mesh.rotation.y += ROT_WRAP;
    if (mesh.rotation.x > ROT_WRAP)  mesh.rotation.x -= ROT_WRAP;
    if (mesh.rotation.x < -ROT_WRAP) mesh.rotation.x += ROT_WRAP;
  });

  return (
    <>
      {/* HDRI provides the reflections that make a crystal look like a crystal.
          "city" gives warm/cool contrast; swap to "studio"/"warehouse"/"dawn" via preset. */}
      <Environment preset="city" environmentIntensity={base.envIntensity} />
      <ambientLight intensity={0.2} />
      <directionalLight position={[5, 8, 3]} intensity={1.2} />

      <mesh ref={meshRef} geometry={geometry}>
        <MeshTransmissionMaterial
          ref={matRef}
          transmission={base.transmission}
          thickness={base.thickness}
          ior={base.ior}
          roughness={base.roughness}
          chromaticAberration={base.chromaticAberration * 4}
          anisotropicBlur={0.1}
          distortion={0.2}
          temporalDistortion={0.1}
          iridescence={base.iridescence}
          iridescenceIOR={1.3}
          iridescenceThicknessRange={[100, 400]}
          clearcoat={1}
          clearcoatRoughness={0}
          samples={6}
          resolution={512}
        />
      </mesh>

      <Atmosphere
        geometry={atmosphereGeo}
        snapRef={snapRef}
        dBotRef={dBotRef}
        dUserRef={dUserRef}
        clickDirRef={clickDirRef}
        clickStrengthRef={clickStrengthRef}
      />
    </>
  );
}
