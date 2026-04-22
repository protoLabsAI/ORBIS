import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { NebulaMaterial } from './materials';
import type { NebulaPreset } from './presets';
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
  TIME_WRAP,
} from '../../shared/constants';
import type { VariantProps } from '../registry';

/**
 * Nebula variant — noise-based volumetric raymarch (FBM value noise,
 * domain warp, Beer-Lambert accumulation, forward-scatter phase).
 * Softer silhouette than the fractal; reacts more organically to voice.
 *
 * Audio reactivity:
 *   - dBot → density pump (σt amplification → cloud gets denser when bot speaks)
 *   - dUser → drift turbulence (the cloud agitates on listening)
 *   - state → color crossfade through shared snapshot
 *   - breath → gentle cloudiness modulation
 */
export function NebulaVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { camera, gl } = useThree();
  const meshRef = useRef<THREE.Mesh>(null);
  const matRef = useRef<InstanceType<typeof NebulaMaterial>>(null);

  const { params } = useOrbState();
  const base = params as unknown as NebulaPreset;

  const { dBotRef, dUserRef } = useAudioEnvelopes({ botStream, localStream });
  const { snapRef } = useStateCrossfade(voiceState, base);
  const { breathNormRef } = useIdleBreath();
  const { clickDirRef, clickStrengthRef, dragVelRef } = usePointerInteraction(meshRef);

  useEffect(() => {
    const m = matRef.current;
    if (!m) return;
    m.uniforms.uCloudScale.value = base.cloudScale;
    m.uniforms.uSoftness.value = base.softness;
  }, [base.cloudScale, base.softness]);

  useEffect(() => {
    gl.setPixelRatio(base.dpr);
  }, [base.dpr, gl]);

  useEffect(() => {
    camera.position.set(0, 0, 13);
  }, [camera]);

  const scratchCam = useMemo(() => new THREE.Vector3(), []);
  const geometry = useMemo(() => new THREE.SphereGeometry(2, 96, 96), []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const m = matRef.current;
    const mesh = meshRef.current;
    const snap = snapRef.current;
    if (!m || !mesh || !snap) return;

    const dBot = dBotRef.current;
    const dUser = dUserRef.current;

    // Density pump on bot speaking; cloudiness mod on state snapshot.
    m.uniforms.uDensity.value = base.density * (0.8 + snap.density * 0.3) + dBot * 1.2;
    m.uniforms.uCloudiness.value =
      base.cloudiness * (1 + breathNormRef.current * BREATH_AMP * 2);
    // Drift turbulence — user voice agitates the flow; bot voice gently accelerates.
    m.uniforms.uDrift.value = base.drift * (1 + dUser * 1.8 + dBot * 0.4);
    m.uniforms.uPrimaryColor.value.copy(snap.primary);
    m.uniforms.uSecondaryColor.value.copy(snap.secondary);
    m.uniforms.uClickDir.value.copy(clickDirRef.current);
    m.uniforms.uClickStrength.value = clickStrengthRef.current;

    // Scale — gentler pump than the fractal; the cloud expands subtly.
    const scale = snap.scale * (1 + breathNormRef.current * BREATH_AMP) * (1 + dBot * 0.04);
    mesh.scale.setScalar(scale);

    // Time + rotation.
    m.uniforms.uTime.value += delta * snap.speed;
    mesh.rotation.y += delta * snap.rotation * ROTATION_SCALE + dragVelRef.current.y * delta;
    mesh.rotation.x += delta * (snap.rotation * 0.5) * ROTATION_SCALE + dragVelRef.current.x * delta;

    if (m.uniforms.uTime.value > TIME_WRAP) m.uniforms.uTime.value -= TIME_WRAP;
    if (mesh.rotation.y > ROT_WRAP)  mesh.rotation.y -= ROT_WRAP;
    if (mesh.rotation.y < -ROT_WRAP) mesh.rotation.y += ROT_WRAP;
    if (mesh.rotation.x > ROT_WRAP)  mesh.rotation.x -= ROT_WRAP;
    if (mesh.rotation.x < -ROT_WRAP) mesh.rotation.x += ROT_WRAP;

    mesh.updateMatrixWorld();
    scratchCam.copy(camera.position);
    mesh.worldToLocal(scratchCam);
    m.uniforms.uLocalCamPos.value.copy(scratchCam);
  });

  return (
    <mesh ref={meshRef} geometry={geometry}>
      <nebulaMaterial
        ref={matRef}
        transparent
        side={THREE.DoubleSide}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
        attach="material"
      />
      <Atmosphere
        geometry={geometry}
        snapRef={snapRef}
        dBotRef={dBotRef}
        dUserRef={dUserRef}
        clickDirRef={clickDirRef}
        clickStrengthRef={clickStrengthRef}
      />
    </mesh>
  );
}
