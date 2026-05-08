import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import {
  GalaxyShellMaterial,
  GalaxyPlasmaMaterial,
  GalaxyParticlesMaterial,
} from './materials';
import type { GalaxyPreset } from './presets';
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
 * Galaxy variant — three-layer composition: a Fresnel-glass shell,
 * a domain-warped Simplex/FBM noise plasma sphere, and a 600-point
 * dust field inside. The plasma is the visual identity; the shell
 * cues "this is enclosed gas, not just a sphere", and the particles
 * give the eye something to track when the plasma rotates slowly.
 *
 * Audio reactivity:
 *   - dBot → plasma brightness boost (the orb glows as it speaks)
 *   - dUser → void threshold perturbation (the gas thins / thickens)
 *   - state → primary/secondary tugs the plasma palette via uVoiceMix
 *   - breath → group scale modulation
 */
export function GalaxyVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { camera, gl } = useThree();
  const groupRef = useRef<THREE.Group>(null);
  const plasmaMatRef = useRef<InstanceType<typeof GalaxyPlasmaMaterial>>(null);
  const shellFrontRef = useRef<InstanceType<typeof GalaxyShellMaterial>>(null);
  const shellBackRef  = useRef<InstanceType<typeof GalaxyShellMaterial>>(null);
  const particlesMatRef = useRef<InstanceType<typeof GalaxyParticlesMaterial>>(null);
  const plasmaMeshRef = useRef<THREE.Mesh>(null);
  // Raycast target — usePointerInteraction needs a Mesh, but our host
  // is a Group with multiple children. Mirror the particles variant
  // pattern: an invisible sphere mesh passed to the hook for click
  // detection. Same dimensions as the shell so click coords align.
  const rayTargetRef = useRef<THREE.Mesh>(null);

  const { base, effectiveState } = useComposedBase<GalaxyPreset>(voiceState);
  const baseRef = useRef(base);
  baseRef.current = base;

  const { dBotRef, dUserRef } = useAudioEnvelopes({ botStream, localStream });
  const { snapRef } = useStateCrossfade(effectiveState, base);
  const { breathNormRef } = useIdleBreath();
  // Particles geometry doesn't drive click reactivity; pointer hook
  // still gives us drag-to-rotate through dragVelRef.
  const { clickDirRef, clickStrengthRef, dragVelRef } = usePointerInteraction(rayTargetRef);

  // Direct (non-state-driven) uniforms.
  useEffect(() => {
    const m = plasmaMatRef.current;
    if (!m) return;
    m.uniforms.uScale.value = base.plasmaScale;
    m.uniforms.uColorDeep.value.set(base.colorDeep);
    m.uniforms.uColorMid.value.set(base.colorMid);
    m.uniforms.uColorBright.value.set(base.colorBright);
    m.uniforms.uVoiceMix.value = base.voiceMix;
  }, [
    base.plasmaScale, base.colorDeep, base.colorMid, base.colorBright,
    base.voiceMix,
  ]);

  useEffect(() => {
    if (shellFrontRef.current) {
      shellFrontRef.current.uniforms.uColor.value.set(base.shellColor);
      shellFrontRef.current.uniforms.uOpacity.value = base.shellOpacity;
    }
    if (shellBackRef.current) {
      // Back shell stays a darker version of the front for depth
      // cueing — derive from the user's choice rather than locking
      // it to a hardcoded colour.
      shellBackRef.current.uniforms.uColor.value
        .set(base.shellColor).multiplyScalar(0.4);
      shellBackRef.current.uniforms.uOpacity.value = base.shellOpacity * 0.7;
    }
  }, [base.shellColor, base.shellOpacity]);

  useEffect(() => {
    if (particlesMatRef.current) {
      particlesMatRef.current.uniforms.uColor.value.set(base.particleColor);
    }
  }, [base.particleColor]);

  useEffect(() => {
    gl.setPixelRatio(base.dpr);
  }, [base.dpr, gl]);

  useEffect(() => {
    camera.position.set(0, 0, 13);
  }, [camera]);

  // Geometries — sized to the orb's working radius (~2 in object space
  // before the group's scale). Plasma sits just inside the shell so the
  // shell silhouette doesn't z-fight with plasma near the edge.
  const shellGeometry = useMemo(() => new THREE.SphereGeometry(2.0, 64, 64), []);
  const plasmaGeometry = useMemo(() => new THREE.SphereGeometry(1.996, 96, 96), []);
  useEffect(() => () => {
    shellGeometry.dispose();
    plasmaGeometry.dispose();
  }, [shellGeometry, plasmaGeometry]);

  // Particle attributes — re-derived when count or radius change.
  // BufferGeometry rebuild on each change is fine for single-digit
  // updates per session; particle count slider isn't expected to
  // be scrubbed continuously.
  const particleGeometry = useMemo(() => {
    const count = Math.max(50, Math.round(base.particleCount));
    const radius = base.particleRadius;
    const positions = new Float32Array(count * 3);
    const sizes = new Float32Array(count);
    for (let i = 0; i < count; i += 1) {
      // Cube-root for uniform volume distribution.
      const r = radius * Math.cbrt(Math.random());
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      positions[i * 3]     = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      positions[i * 3 + 2] = r * Math.cos(phi);
      sizes[i] = Math.random();
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('aSize', new THREE.BufferAttribute(sizes, 1));
    return geo;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base.particleCount, base.particleRadius]);
  useEffect(() => () => particleGeometry.dispose(), [particleGeometry]);

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const plasmaMat = plasmaMatRef.current;
    const particlesMat = particlesMatRef.current;
    const group = groupRef.current;
    const plasmaMesh = plasmaMeshRef.current;
    const snap = snapRef.current;
    if (!plasmaMat || !particlesMat || !group || !plasmaMesh || !snap) return;

    const dBot = dBotRef.current;
    const dUser = dUserRef.current;

    // Plasma brightness rides snap.density and dBot.
    plasmaMat.uniforms.uBrightness.value =
      base.plasmaBrightness * (0.85 + snap.density * 0.25) + dBot * 0.4;

    // Voids pulse on user voice — the gas thins out slightly when
    // listening (subtle; capped low so the silhouette stays steady).
    plasmaMat.uniforms.uThreshold.value = base.voidThreshold + dUser * 0.05;

    // Voice tint colours.
    plasmaMat.uniforms.uPrimaryColor.value.copy(snap.primary);
    plasmaMat.uniforms.uSecondaryColor.value.copy(snap.secondary);
    plasmaMat.uniforms.uClickDir.value.copy(clickDirRef.current);
    plasmaMat.uniforms.uClickStrength.value = clickStrengthRef.current;

    // Time advances.
    plasmaMat.uniforms.uTime.value += delta * snap.speed;
    particlesMat.uniforms.uTime.value += delta;
    if (plasmaMat.uniforms.uTime.value > TIME_WRAP)
      plasmaMat.uniforms.uTime.value -= TIME_WRAP;
    if (particlesMat.uniforms.uTime.value > TIME_WRAP)
      particlesMat.uniforms.uTime.value -= TIME_WRAP;

    // Group scale: state × breath × audio pump.
    const scale = snap.scale * (1 + breathNormRef.current * BREATH_AMP) * (1 + dBot * 0.06);
    group.scale.setScalar(scale);

    // Plasma mesh has its own slow drift inside the group (independent
    // axis) — replicates the source's `plasmaMesh.rotation.y = t * 0.08`.
    plasmaMesh.rotation.y += delta * 0.08;

    // Group rotation — auto-rotate + drag inertia.
    group.rotation.y += delta * snap.rotation * ROTATION_SCALE + dragVelRef.current.y * delta;
    group.rotation.x += delta * (snap.rotation * 0.5) * ROTATION_SCALE + dragVelRef.current.x * delta;
    if (group.rotation.y > ROT_WRAP)  group.rotation.y -= ROT_WRAP;
    if (group.rotation.y < -ROT_WRAP) group.rotation.y += ROT_WRAP;
    if (group.rotation.x > ROT_WRAP)  group.rotation.x -= ROT_WRAP;
    if (group.rotation.x < -ROT_WRAP) group.rotation.x += ROT_WRAP;
  });

  return (
    <group ref={groupRef}>
      {/* Invisible raycast target — usePointerInteraction needs a
          Mesh; the group is the host but the hook treats it as a
          single object for hit-testing. */}
      <mesh ref={rayTargetRef} geometry={shellGeometry} visible={false} />
      {/* Shell back (BackSide) — the dim half-sphere "behind" the
          plasma when looking through. */}
      <mesh geometry={shellGeometry}>
        <galaxyShellMaterial
          ref={shellBackRef}
          transparent
          side={THREE.BackSide}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
          attach="material"
        />
      </mesh>
      {/* Shell front (FrontSide) — the user-coloured outer envelope. */}
      <mesh geometry={shellGeometry}>
        <galaxyShellMaterial
          ref={shellFrontRef}
          transparent
          side={THREE.FrontSide}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
          attach="material"
        />
      </mesh>
      {/* Plasma — the volumetric-looking gas inside. Has its own
          slow rotation independent of the group's auto-rotate. */}
      <mesh ref={plasmaMeshRef} geometry={plasmaGeometry}>
        <galaxyPlasmaMaterial
          ref={plasmaMatRef}
          transparent
          side={THREE.DoubleSide}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
          attach="material"
        />
      </mesh>
      {/* Dust particles distributed inside the orb. */}
      <points geometry={particleGeometry}>
        <galaxyParticlesMaterial
          ref={particlesMatRef}
          transparent
          blending={THREE.AdditiveBlending}
          depthWrite={false}
          attach="material"
        />
      </points>
    </group>
  );
}
