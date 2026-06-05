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
 * Disco variant — a faceted mirror ball reflecting a neon environment. A solid
 * (opaque) sphere mesh whose surface shader quantises the normal into mirror
 * tiles and reflects the procedural neon "atmosphere"; the mesh spin turns the
 * ball. State → neon tints + brightness; bot voice → brightness pump. The
 * static 3rd accent colour comes from the palette (not state-crossfaded).
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

  // Disco uniforms (incl. the static accent colour) — applied on change.
  useEffect(() => {
    const m = matRef.current;
    if (!m) return;
    m.uniforms.uColor3.value.set(base.tertiaryEnergy);
    m.uniforms.uFacets.value = base.facets;
    m.uniforms.uRoughness.value = base.roughness;
    m.uniforms.uFractalScale.value = base.fractalScale;
    m.uniforms.uFractalAmp.value = base.fractalAmp;
    m.uniforms.uBgSpeed.value = base.bgSpeed;
    m.uniforms.uCoreSpeed.value = base.coreSpeed;
  }, [
    base.tertiaryEnergy, base.facets, base.roughness, base.fractalScale,
    base.fractalAmp, base.bgSpeed, base.coreSpeed,
  ]);

  useEffect(() => { gl.setPixelRatio(base.dpr); }, [base.dpr, gl]);
  useEffect(() => { camera.position.set(0, 0, 13); }, [camera]);

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

    m.uniforms.uColor1.value.copy(snap.primary);
    m.uniforms.uColor2.value.copy(snap.secondary);
    m.uniforms.uBrightness.value = base.brightness * (0.7 + snap.glow) * (1 + dBot * 0.4);

    const scale = snap.scale * (1 + breathNormRef.current * BREATH_AMP) * (1 + dBot * 0.05);
    mesh.scale.setScalar(scale);

    m.uniforms.uTime.value += delta * snap.speed * base.speed;
    mesh.rotation.y += delta * snap.rotation * ROTATION_SCALE + dragVelRef.current.y * delta;
    mesh.rotation.x += delta * snap.rotation * 0.5 * ROTATION_SCALE + dragVelRef.current.x * delta;

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
      <discoMaterial ref={matRef} attach="material" />
    </mesh>
  );
}
