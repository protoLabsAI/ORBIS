import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { DiscoMaterial } from './materials';
import type { DiscoPreset } from './presets';
import { useAudioEnvelopes } from '../../shared/hooks/useAudioEnvelopes';
import { useStateCrossfade } from '../../shared/hooks/useStateCrossfade';
import { useIdleBreath } from '../../shared/hooks/useIdleBreath';
import { usePointerInteraction } from '../../shared/hooks/usePointerInteraction';
import { useComposedBase } from '../../shared/hooks/useComposedBase';
import { BREATH_AMP, MAX_DELTA_S, ROT_WRAP, ROTATION_SCALE, TIME_WRAP } from '../../shared/constants';
import type { VariantProps } from '../registry';

/**
 * Disco variant — a KIFS fractal raymarched inside the orb's glass shell.
 * Mirrors the fractal driver: shared hooks own state/audio/breath/pointer, the
 * sphere mesh's rotation drives the spin (so the shader needs no rotation
 * matrix), and only the KIFS uniforms are disco-specific. Bloom via DiscoPost.
 */
export function DiscoVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { camera, gl } = useThree();
  const meshRef = useRef<THREE.Mesh>(null);
  const matRef = useRef<InstanceType<typeof DiscoMaterial>>(null);

  const { base, effectiveState } = useComposedBase<DiscoPreset>(voiceState);
  const { dBotRef, dUserRef } = useAudioEnvelopes({ botStream, localStream });
  const { snapRef } = useStateCrossfade(effectiveState, base);
  const { breathNormRef } = useIdleBreath();
  const { dragVelRef } = usePointerInteraction(meshRef);
  void dUserRef;

  // KIFS geometry uniforms — applied on change.
  useEffect(() => {
    const m = matRef.current;
    if (!m) return;
    m.uniforms.uFoldSteps.value = base.foldSteps;
    m.uniforms.uKifsScale.value = base.kifsScale;
    m.uniforms.uKifsOffset.value = base.kifsOffset;
    m.uniforms.uTextureScale.value = base.textureScale;
    m.uniforms.uLightSpeed.value = base.lightSpeed;
    m.uniforms.uMaxSteps.value = base.maxSteps;
    m.uniforms.uAutoRotationSpeed.value = base.autoRotationSpeed;
    m.uniforms.uShellInner.value = base.shellInner;
  }, [
    base.foldSteps, base.kifsScale, base.kifsOffset, base.textureScale,
    base.lightSpeed, base.maxSteps, base.autoRotationSpeed, base.shellInner,
  ]);

  useEffect(() => { gl.setPixelRatio(base.dpr); }, [base.dpr, gl]);
  useEffect(() => { camera.position.set(0, 0, 13); }, [camera]);

  const scratchCam = useMemo(() => new THREE.Vector3(), []);
  const geometry = useMemo(() => new THREE.SphereGeometry(2, 64, 64), []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const m = matRef.current;
    const mesh = meshRef.current;
    const snap = snapRef.current;
    if (!m || !mesh || !snap) return;

    const dBot = dBotRef.current;

    // Emissive tint = state primary × colorBoost; brightness from state glow + voice.
    m.uniforms.uBaseColor.value.copy(snap.primary).multiplyScalar(base.colorBoost);
    m.uniforms.uSecondaryColor.value.copy(snap.secondary);
    m.uniforms.uBrightness.value = base.brightness * (0.7 + snap.glow) * (1 + dBot * 0.4);

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
      <discoMaterial
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
