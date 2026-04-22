import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { FractalMaterial } from './materials';
import type { FractalPreset } from './presets';
import { useOrbState } from '../../useOrbState';
import { useAudioEnvelopes } from '../../shared/hooks/useAudioEnvelopes';
import { useStateCrossfade } from '../../shared/hooks/useStateCrossfade';
import { useIdleBreath } from '../../shared/hooks/useIdleBreath';
import { usePointerInteraction } from '../../shared/hooks/usePointerInteraction';
import { Atmosphere } from '../../shared/atmosphere/Atmosphere';
import { clamp01 } from '../../shared/math';
import {
  BREATH_AMP,
  MAX_DELTA_S,
  ROT_WRAP,
  ROTATION_SCALE,
  TIME_WRAP,
} from '../../shared/constants';
import type { VariantProps } from '../registry';

/**
 * Fractal variant — ray-marched volumetric fractal + shared atmosphere.
 * Composition-only: delegates state/audio/breath/pointer to shared hooks
 * and only owns fractal-specific uniform updates.
 */
export function FractalVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { camera, gl } = useThree();
  const meshRef = useRef<THREE.Mesh>(null);
  const matRef = useRef<InstanceType<typeof FractalMaterial>>(null);

  const { params } = useOrbState();
  const base = params as unknown as FractalPreset;
  const baseRef = useRef(base);
  baseRef.current = base;

  // Shared driver.
  const { dBotRef, dUserRef } = useAudioEnvelopes({ botStream, localStream });
  const { snapRef } = useStateCrossfade(voiceState, base);
  const { breathNormRef } = useIdleBreath();
  const { clickDirRef, clickStrengthRef, dragVelRef } = usePointerInteraction(meshRef);

  // Direct (non-state-driven) uniforms — applied on change.
  useEffect(() => {
    const m = matRef.current;
    if (!m) return;
    m.uniforms.uFractalIters.value = base.fractalIters;
    m.uniforms.uFractalScale.value = base.fractalScale;
    m.uniforms.uFractalDecay.value = base.fractalDecay;
    m.uniforms.uSmoothness.value   = base.smoothness;
    m.uniforms.uInternalAnim.value = base.internalAnim;
  }, [
    base.fractalIters, base.fractalScale, base.fractalDecay,
    base.smoothness, base.internalAnim,
  ]);

  useEffect(() => {
    gl.setPixelRatio(base.dpr);
  }, [base.dpr, gl]);

  useEffect(() => {
    camera.position.set(0, 0, 13);
  }, [camera]);

  // Scratch vec for the local-cam uniform.
  const scratchCam = useMemo(() => new THREE.Vector3(), []);
  const geometry = useMemo(() => new THREE.SphereGeometry(2, 128, 128), []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const m = matRef.current;
    const mesh = meshRef.current;
    const snap = snapRef.current;
    if (!m || !mesh || !snap) return;

    const dBot = dBotRef.current;
    const dUser = dUserRef.current;

    // Fractal-specific uniform composition: state-base + audio modulation.
    m.uniforms.uDensity.value   = snap.density + dBot * 0.9;
    m.uniforms.uAsymmetry.value = clamp01(snap.asymmetry + dUser * 0.06);
    m.uniforms.uPrimaryColor.value.copy(snap.primary);
    m.uniforms.uSecondaryColor.value.copy(snap.secondary);
    m.uniforms.uClickDir.value.copy(clickDirRef.current);
    m.uniforms.uClickStrength.value = clickStrengthRef.current;

    // Scale: state × breath × gentle audio pump.
    const scale = snap.scale * (1 + breathNormRef.current * BREATH_AMP) * (1 + dBot * 0.06);
    mesh.scale.setScalar(scale);

    // Time + rotation.
    m.uniforms.uTime.value += delta * snap.speed;
    mesh.rotation.y += delta * snap.rotation * ROTATION_SCALE + dragVelRef.current.y * delta;
    mesh.rotation.x += delta * (snap.rotation * 0.5) * ROTATION_SCALE + dragVelRef.current.x * delta;

    // Float32 wrap — see constants.ts TIME_WRAP / ROT_WRAP.
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
      <fractalMaterial
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
