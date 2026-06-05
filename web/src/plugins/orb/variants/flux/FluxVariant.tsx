import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { FluxMaterial } from './materials';
import type { FluxPreset } from './presets';
import { useAudioEnvelopes } from '../../shared/hooks/useAudioEnvelopes';
import { useStateCrossfade } from '../../shared/hooks/useStateCrossfade';
import { useIdleBreath } from '../../shared/hooks/useIdleBreath';
import { usePointerInteraction } from '../../shared/hooks/usePointerInteraction';
import { useComposedBase } from '../../shared/hooks/useComposedBase';
import { BREATH_AMP, MAX_DELTA_S, ROT_WRAP, ROTATION_SCALE, TIME_WRAP } from '../../shared/constants';
import type { VariantProps } from '../registry';

/**
 * Flux variant — a fluid, pulsing volumetric SDF fractal glowing inside the
 * orb's shell. Same raymarch driver as Reactor; the difference is the emission
 * colour. Per design it does NOT spin hue continuously — instead the CPU slowly
 * cycles uColorBase between three palette colours (state primary → state
 * secondary → static accent) and bakes brightness (state glow + bot voice) into
 * its magnitude. Additive glow + Bloom (FluxPost).
 */
export function FluxVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { camera, gl } = useThree();
  const meshRef = useRef<THREE.Mesh>(null);
  const matRef = useRef<InstanceType<typeof FluxMaterial>>(null);

  const { base, effectiveState } = useComposedBase<FluxPreset>(voiceState);
  const { dBotRef, dUserRef } = useAudioEnvelopes({ botStream, localStream });
  const { snapRef } = useStateCrossfade(effectiveState, base);
  const { breathNormRef } = useIdleBreath();
  const { dragVelRef } = usePointerInteraction(meshRef);
  void dUserRef;

  // SDF / glow uniforms — applied on change.
  useEffect(() => {
    const m = matRef.current;
    if (!m) return;
    m.uniforms.uIterations.value = base.iterations;
    m.uniforms.uDistortion.value = base.distortion;
    m.uniforms.uPulseIntensity.value = base.pulseIntensity;
    m.uniforms.uFractalScale.value = base.fractalScale;
  }, [base.iterations, base.distortion, base.pulseIntensity, base.fractalScale]);

  useEffect(() => { gl.setPixelRatio(base.dpr); }, [base.dpr, gl]);
  useEffect(() => { camera.position.set(0, 0, 13); }, [camera]);

  const scratchCam = useMemo(() => new THREE.Vector3(), []);
  const scratchColor = useMemo(() => new THREE.Color(), []);
  const tertiary = useMemo(() => new THREE.Color(), []);
  useEffect(() => { tertiary.set(base.tertiaryEnergy); }, [base.tertiaryEnergy, tertiary]);
  const geometry = useMemo(() => new THREE.SphereGeometry(2, 64, 64), []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  const cycleRef = useRef(0);

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const m = matRef.current;
    const mesh = meshRef.current;
    const snap = snapRef.current;
    if (!m || !mesh || !snap) return;

    const dBot = dBotRef.current;

    // --- colour cycle: lerp through the three palette colours (no hue-spin) ---
    cycleRef.current = (cycleRef.current + delta * base.cycleSpeed) % 3;
    const phase = cycleRef.current;
    const i0 = Math.floor(phase);
    const f = phase - i0;
    const cA = i0 === 0 ? snap.primary : i0 === 1 ? snap.secondary : tertiary;
    const cB = i0 === 0 ? snap.secondary : i0 === 1 ? tertiary : snap.primary;
    scratchColor.copy(cA).lerp(cB, f);
    const bright = base.brightness * (0.7 + snap.glow) * (1 + dBot * 0.4);
    m.uniforms.uColorBase.value.copy(scratchColor).multiplyScalar(bright);

    // Scale: state × breath × gentle audio pump.
    const scale = snap.scale * (1 + breathNormRef.current * BREATH_AMP) * (1 + dBot * 0.05);
    mesh.scale.setScalar(scale);

    // Time + rotation (the mesh spin rotates the fractal with it).
    m.uniforms.uTime.value += delta * snap.speed * base.speed;
    mesh.rotation.y += delta * snap.rotation * ROTATION_SCALE + dragVelRef.current.y * delta;
    mesh.rotation.x += delta * snap.rotation * 0.5 * ROTATION_SCALE + dragVelRef.current.x * delta;

    if (m.uniforms.uTime.value > TIME_WRAP) m.uniforms.uTime.value -= TIME_WRAP;
    if (mesh.rotation.y > ROT_WRAP)  mesh.rotation.y -= ROT_WRAP;
    if (mesh.rotation.y < -ROT_WRAP) mesh.rotation.y += ROT_WRAP;
    if (mesh.rotation.x > ROT_WRAP)  mesh.rotation.x -= ROT_WRAP;
    if (mesh.rotation.x < -ROT_WRAP) mesh.rotation.x += ROT_WRAP;

    // Camera in mesh-local space → raymarch ray origin.
    mesh.updateMatrixWorld();
    scratchCam.copy(camera.position);
    mesh.worldToLocal(scratchCam);
    m.uniforms.uLocalCamPos.value.copy(scratchCam);
  });

  return (
    <mesh ref={meshRef} geometry={geometry}>
      <fluxMaterial
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
