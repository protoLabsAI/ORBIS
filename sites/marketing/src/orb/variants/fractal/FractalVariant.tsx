import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { FractalMaterial } from './materials';
import { FRACTAL_PRESETS, type FractalPreset } from './presets';
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
import type { VoiceState } from '../../shared/stateSnapshot';

const AURORA = FRACTAL_PRESETS['Aurora'];

// Map FractalPreset → OrbBasePreset shape that useStateCrossfade expects
function presetToBase(p: FractalPreset) {
  return {
    density: p.density,
    atmosphereGlow: p.atmosphereGlow,
    speed: p.speed,
    chromaticAberration: p.chromaticAberration,
    asymmetry: p.asymmetry,
    orbRotation: p.orbRotation,
    primaryEnergy: p.primaryEnergy,
    secondaryEnergy: p.secondaryEnergy,
  };
}

export function FractalVariant({ voiceState }: { voiceState: VoiceState }) {
  const { camera, gl } = useThree();
  const meshRef = useRef<THREE.Mesh>(null);
  const matRef = useRef<InstanceType<typeof FractalMaterial>>(null);

  const base = presetToBase(AURORA);
  const { snapRef } = useStateCrossfade(voiceState, base);
  const { breathNormRef } = useIdleBreath();
  const { clickDirRef, clickStrengthRef, dragVelRef } = usePointerInteraction(meshRef);

  // Audio stubs — marketing orb has no mic
  const dBotRef = useRef(0);
  const dUserRef = useRef(0);

  useEffect(() => {
    const m = matRef.current;
    if (!m) return;
    m.uniforms.uFractalIters.value = AURORA.fractalIters;
    m.uniforms.uFractalScale.value = AURORA.fractalScale;
    m.uniforms.uFractalDecay.value = AURORA.fractalDecay;
    m.uniforms.uSmoothness.value   = AURORA.smoothness;
    m.uniforms.uInternalAnim.value = AURORA.internalAnim;
  }, []);

  useEffect(() => { gl.setPixelRatio(Math.min(AURORA.dpr, 1.5)); }, [gl]);
  useEffect(() => { camera.position.set(0, 0, 13); }, [camera]);

  const scratchCam = useMemo(() => new THREE.Vector3(), []);
  const geometry = useMemo(() => new THREE.SphereGeometry(2, 128, 128), []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const m = matRef.current;
    const mesh = meshRef.current;
    const snap = snapRef.current;
    if (!m || !mesh || !snap) return;

    m.uniforms.uDensity.value   = snap.density;
    m.uniforms.uAsymmetry.value = clamp01(snap.asymmetry);
    m.uniforms.uPrimaryColor.value.copy(snap.primary);
    m.uniforms.uSecondaryColor.value.copy(snap.secondary);
    m.uniforms.uClickDir.value.copy(clickDirRef.current);
    m.uniforms.uClickStrength.value = clickStrengthRef.current;

    const scale = snap.scale * (1 + breathNormRef.current * BREATH_AMP);
    mesh.scale.setScalar(scale);

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
        atmosphereLevel={AURORA.atmosphereLevel}
        atmosphereScale={AURORA.atmosphereScale}
      />
    </mesh>
  );
}
