import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { LiquidMaterial } from './materials';
import type { LiquidPreset } from './presets';
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
 * Liquid variant — sphere-mounted port of a hard-surface raymarched
 * orb. The ONLY ORBIS variant that finds a single surface hit and
 * shades with normals + Phong-style diffuse / fill / specular / rim
 * (the rest accumulate volume). Reads as mercury / oil-puddle — a
 * physical "skin" rather than a glowing field.
 *
 * Audio reactivity:
 *   - dBot  → height-amp boost (the surface bulges as the orb speaks)
 *   - dUser → warp-amp bump (the skin agitates during listen)
 *   - state → primary/secondary lightly tints the procedural palette
 *   - breath → mesh scale modulation
 */
export function LiquidVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { camera, gl } = useThree();
  const meshRef = useRef<THREE.Mesh>(null);
  const matRef = useRef<InstanceType<typeof LiquidMaterial>>(null);

  const { base, effectiveState } = useComposedBase<LiquidPreset>(voiceState);
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
    m.uniforms.uSphereSize.value      = base.sphereSize;
    m.uniforms.uWarpFalloff.value     = base.warpFalloff;
    m.uniforms.uWarpStartFreq.value   = base.warpStartFreq;
    m.uniforms.uWarpSteps.value       = base.warpSteps;
    m.uniforms.uWarpVelocity.value    = base.warpVelocity;
    m.uniforms.uNoiseContrast.value   = base.noiseContrast;
    m.uniforms.uColor1.value.set(base.liquidColor1);
    m.uniforms.uColor2.value.set(base.liquidColor2);
    m.uniforms.uColor3.value.set(base.liquidColor3);
    m.uniforms.uColor4.value.set(base.liquidColor4);
    m.uniforms.uAmbient.value           = base.ambient;
    m.uniforms.uDiffuse.value           = base.diffuse;
    m.uniforms.uFillLight.value         = base.fillLight;
    m.uniforms.uSpecularPower.value     = base.specularPower;
    m.uniforms.uSpecularIntensity.value = base.specularIntensity;
  }, [
    base.sphereSize, base.warpFalloff, base.warpStartFreq,
    base.warpSteps, base.warpVelocity, base.noiseContrast,
    base.liquidColor1, base.liquidColor2, base.liquidColor3, base.liquidColor4,
    base.ambient, base.diffuse, base.fillLight,
    base.specularPower, base.specularIntensity,
  ]);

  useEffect(() => {
    gl.setPixelRatio(base.dpr);
  }, [base.dpr, gl]);

  useEffect(() => {
    camera.position.set(0, 0, 13);
  }, [camera]);

  const scratchCam = useMemo(() => new THREE.Vector3(), []);
  // Sphere mesh sized to comfortably contain the displaced surface
  // (sphereSize max=2 + heightAmp max=0.8 → ~2.8 max radius).
  // Radius 4 → no edge clipping. Mesh is invisible; the SDF's hit
  // test inside is what carves the visible surface.
  const geometry = useMemo(() => new THREE.SphereGeometry(4.0, 32, 32), []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const m = matRef.current;
    const mesh = meshRef.current;
    const snap = snapRef.current;
    if (!m || !mesh || !snap) return;

    const dBot = dBotRef.current;
    const dUser = dUserRef.current;

    // Height amp swells with bot speech — surface bulges as orb talks.
    m.uniforms.uHeightAmp.value = base.heightAmp + dBot * 0.10;

    // Warp amp tracks user energy — skin agitates during listen.
    m.uniforms.uWarpAmp.value = base.warpAmp + dUser * 0.20;

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
      <liquidMaterial
        ref={matRef}
        transparent
        side={THREE.DoubleSide}
        depthWrite={false}
        attach="material"
      />
    </mesh>
  );
}
