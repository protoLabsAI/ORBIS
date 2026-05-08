import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { SpectrumMaterial } from './materials';
import type { SpectrumPreset } from './presets';
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
 * Spectrum variant — sphere-mounted port of an organic volumetric
 * raymarch through two smooth-min-blended SDFs with nested sine-wave
 * turbulence. The cosine-palette emission per ray-step gives the
 * rainbow gradient through the volume; the soft fade between
 * uFadeInner / uFadeOuter is what produces the halo silhouette.
 *
 * Audio reactivity:
 *   - dBot  → glow boost (the orb brightens while speaking)
 *   - dUser → smoothing perturbation (the blob breathes during listen)
 *   - state → primary/secondary crossfade lightly tints the rainbow
 *   - breath → mesh scale modulation
 */
export function SpectrumVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { camera, gl } = useThree();
  const meshRef = useRef<THREE.Mesh>(null);
  const matRef = useRef<InstanceType<typeof SpectrumMaterial>>(null);

  const { base, effectiveState } = useComposedBase<SpectrumPreset>(voiceState);
  const baseRef = useRef(base);
  baseRef.current = base;

  const { dBotRef, dUserRef } = useAudioEnvelopes({ botStream, localStream });
  const { snapRef } = useStateCrossfade(effectiveState, base);
  const { breathNormRef } = useIdleBreath();
  const { clickDirRef, clickStrengthRef, dragVelRef } = usePointerInteraction(meshRef);

  // Direct (non-state-driven) uniforms.
  useEffect(() => {
    const m = matRef.current;
    if (!m) return;
    m.uniforms.uFractalScale.value = base.fractalScale;
    m.uniforms.uFadeOuter.value    = base.fadeOuter;
    m.uniforms.uFadeInner.value    = base.fadeInner;
    m.uniforms.uColorPhases.value.set(
      base.colorPhaseR,
      base.colorPhaseG,
      base.colorPhaseB,
      base.colorPhaseA,
    );
  }, [
    base.fractalScale, base.fadeOuter, base.fadeInner,
    base.colorPhaseR, base.colorPhaseG, base.colorPhaseB, base.colorPhaseA,
  ]);

  useEffect(() => {
    gl.setPixelRatio(base.dpr);
  }, [base.dpr, gl]);

  useEffect(() => {
    camera.position.set(0, 0, 13);
  }, [camera]);

  const scratchCam = useMemo(() => new THREE.Vector3(), []);
  // Sphere mesh sized to comfortably contain the largest fadeOuter
  // (max=5 on the schema). Radius 5.5 → no edge clipping. Mesh is
  // invisible; alpha falls off where the soft-fade carves nothing.
  const geometry = useMemo(() => new THREE.SphereGeometry(5.5, 32, 32), []);
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
    m.uniforms.uGlow.value = base.glow * (0.85 + snap.density * 0.25) + dBot * 0.5;

    // Smoothing perturbation — user energy makes the blob "breathe"
    // (lower smoothing = sharper boundary). Capped low so the orb
    // silhouette doesn't visibly wobble.
    m.uniforms.uSmoothing.value = base.smoothing + dUser * 0.4;

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
      <spectrumMaterial
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
