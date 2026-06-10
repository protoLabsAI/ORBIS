import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import type { OrbDefinition } from '../definition/types';
import { buildFragmentSource, vertexSource } from '../definition/prelude';
import { buildUniforms } from './uniforms';
import { BindingRuntime } from './bindings';
import type { SignalContext } from './signals';
import { coerceBasePreset } from '../stateSnapshot';
import { useStateCrossfade } from '../hooks/useStateCrossfade';
import { useIdleBreath } from '../hooks/useIdleBreath';
import { usePointerInteraction } from '../hooks/usePointerInteraction';
import {
  BREATH_AMP,
  MAX_DELTA_S,
  ROT_WRAP,
  ROTATION_SCALE,
  TIME_WRAP,
} from '../constants';
import type { Mood, ParamMap, VoiceState } from '../types';

export interface DefinitionOrbProps {
  definition: OrbDefinition;
  voiceState: VoiceState;
  /** Composed params for this frame (host runs its own composition). */
  base: ParamMap;
  /** Live mood for mood.* signals. Defaults to neutral. */
  mood?: Mood;
  /** Enveloped [0,1] speech levels, written by the host per frame. */
  botLevelRef?: React.RefObject<number>;
  userLevelRef?: React.RefObject<number>;
}

const NEUTRAL_MOOD: Mood = { valence: 0, arousal: 0, guardedness: 0 };
const ZERO_REF: React.RefObject<number> = { current: 0 };

/**
 * raymarch-v1 — the generic data-driven orb renderer. Does exactly what
 * the hand-written raymarched variants (spectrum / nebula / fractal /
 * crystal …) do per frame, driven by an OrbDefinition instead of code:
 * state-crossfaded snapshot, idle breath, pointer drag/click, engine-
 * managed uTime / uLocalCamPos, and the definition's declarative
 * signal→uniform bindings.
 */
export function DefinitionOrb({
  definition,
  voiceState,
  base,
  mood,
  botLevelRef,
  userLevelRef,
}: DefinitionOrbProps) {
  const { camera, gl } = useThree();
  const meshRef = useRef<THREE.Mesh>(null);

  const coerced = coerceBasePreset(base);
  const baseRef = useRef(base);
  baseRef.current = base;
  const moodRef = useRef(mood ?? NEUTRAL_MOOD);
  moodRef.current = mood ?? NEUTRAL_MOOD;

  const { snapRef } = useStateCrossfade(voiceState, coerced);
  const { breathNormRef } = useIdleBreath();
  const { clickDirRef, clickStrengthRef, dragVelRef } = usePointerInteraction(meshRef);

  const material = useMemo(() => {
    const m = new THREE.ShaderMaterial({
      uniforms: buildUniforms(definition),
      vertexShader: vertexSource(definition),
      fragmentShader: buildFragmentSource(definition),
      transparent: definition.material?.transparent ?? true,
      depthWrite: definition.material?.depthWrite ?? false,
      side: (definition.material?.doubleSide ?? true) ? THREE.DoubleSide : THREE.FrontSide,
      blending:
        (definition.material?.blending ?? 'additive') === 'additive'
          ? THREE.AdditiveBlending
          : THREE.NormalBlending,
    });
    return m;
  }, [definition]);
  useEffect(() => () => material.dispose(), [material]);

  const geometry = useMemo(() => {
    const g = definition.geometry;
    return new THREE.SphereGeometry(
      g?.radius ?? 5.5,
      g?.widthSegments ?? 32,
      g?.heightSegments ?? 32,
    );
  }, [definition]);
  useEffect(() => () => geometry.dispose(), [geometry]);

  const runtime = useMemo(() => new BindingRuntime(definition), [definition]);

  // dpr follows the standard perf param when present.
  const dpr = typeof base.dpr === 'number' ? base.dpr : undefined;
  useEffect(() => {
    if (dpr !== undefined) gl.setPixelRatio(dpr);
  }, [dpr, gl]);

  useEffect(() => {
    camera.position.set(0, 0, 13);
  }, [camera]);

  const timeRef = useRef(0);
  const scratchCam = useMemo(() => new THREE.Vector3(), []);
  const motion = definition.motion;

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const mesh = meshRef.current;
    const snap = snapRef.current;
    if (!mesh || !snap) return;
    const uniforms = material.uniforms;

    const botLevel = (botLevelRef ?? ZERO_REF).current ?? 0;
    const userLevel = (userLevelRef ?? ZERO_REF).current ?? 0;

    // Engine-managed time: speed-scaled, wrapped against float32 drift.
    timeRef.current += delta * snap.speed;
    if (timeRef.current > TIME_WRAP) timeRef.current -= TIME_WRAP;
    uniforms.uTime.value = timeRef.current;

    // Implicit defaults — bindings overwrite these when the definition
    // binds the same target.
    (uniforms.uPrimaryColor.value as THREE.Color).copy(snap.primary);
    (uniforms.uSecondaryColor.value as THREE.Color).copy(snap.secondary);
    (uniforms.uClickDir.value as THREE.Vector3).copy(clickDirRef.current);
    uniforms.uClickStrength.value = clickStrengthRef.current;

    const ctx: SignalContext = {
      time: timeRef.current,
      botLevel,
      userLevel,
      breath: breathNormRef.current,
      clickStrength: clickStrengthRef.current,
      mood: moodRef.current,
      snap,
      params: baseRef.current,
    };
    runtime.apply(ctx, uniforms);

    // Mesh scale: state × breath × audio pump (all declaratively tunable).
    const breathOn = motion?.breath ?? true;
    const pump = motion?.scaleAudioPump ?? 0.06;
    const scale =
      snap.scale *
      (breathOn ? 1 + breathNormRef.current * BREATH_AMP : 1) *
      (1 + botLevel * pump);
    mesh.scale.setScalar(scale);

    // Rotation: state auto-rotate + drag momentum.
    const xRatio = motion?.rotationXRatio ?? 0.5;
    mesh.rotation.y += delta * snap.rotation * ROTATION_SCALE + dragVelRef.current.y * delta;
    mesh.rotation.x +=
      delta * (snap.rotation * xRatio) * ROTATION_SCALE + dragVelRef.current.x * delta;
    if (mesh.rotation.y > ROT_WRAP) mesh.rotation.y -= ROT_WRAP;
    if (mesh.rotation.y < -ROT_WRAP) mesh.rotation.y += ROT_WRAP;
    if (mesh.rotation.x > ROT_WRAP) mesh.rotation.x -= ROT_WRAP;
    if (mesh.rotation.x < -ROT_WRAP) mesh.rotation.x += ROT_WRAP;

    // Local-space camera for the raymarch ray origin.
    mesh.updateMatrixWorld();
    scratchCam.copy(camera.position);
    mesh.worldToLocal(scratchCam);
    (uniforms.uLocalCamPos.value as THREE.Vector3).copy(scratchCam);
  });

  return <mesh ref={meshRef} geometry={geometry} material={material} />;
}
