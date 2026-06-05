import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { useAudioEnvelopes } from '../../shared/hooks/useAudioEnvelopes';
import { useStateCrossfade } from '../../shared/hooks/useStateCrossfade';
import { useIdleBreath } from '../../shared/hooks/useIdleBreath';
import { usePointerInteraction } from '../../shared/hooks/usePointerInteraction';
import { useComposedBase } from '../../shared/hooks/useComposedBase';
import { BREATH_AMP, MAX_DELTA_S, ROT_WRAP, ROTATION_SCALE } from '../../shared/constants';
import type { EdisonPreset } from './presets';
import type { VariantProps } from '../registry';

/**
 * Edison variant — a glass capsule housing writhing filament "tentacles" of
 * light with particles streaming along them, a living Edison bulb. Ported from
 * the imperative lil-gui prototype into the shared signal bus:
 *
 *   state  → filament colour crossfade + group scale/rotation (snap)
 *   dBot   → writhe amplitude + flow speed boost (energy when speaking)
 *   breath → subtle scale modulation
 *
 * Bloom is contributed via EdisonPost (rendered in OrbStage's composer).
 */

// Built at prototype scale, then the host group is scaled to orb framing.
const ORB_SCALE = 0.28;
const CAPSULE_RADIUS = 5.0;
const CAPSULE_LENGTH = 9.0;
const POINTS_PER_LINE = 70;
const CORE_LINES = 3;
const TAPER = 4.0;

interface TentacleBase {
  baseX: number;
  baseZ: number;
  phaseX: number;
  phaseY: number;
  phaseZ: number;
}

interface LineDatum {
  line: THREE.Line;
  tentacleIndex: number;
  localOffsetX: number;
  localOffsetZ: number;
  microPhase: number;
}

interface ParticleDatum {
  tentacleIndex: number;
  t: number;
  speed: number;
  angle: number;
  radiusBase: number;
  orbitSpeed: number;
}

interface EdisonScene {
  objects: THREE.Object3D[];
  bases: TentacleBase[];
  lineData: LineDatum[];
  particles: THREE.Points;
  particleData: ParticleDatum[];
  coreMat: THREE.LineBasicMaterial;
  glowMat: THREE.LineBasicMaterial;
  particleMat: THREE.PointsMaterial;
  capsuleMat: THREE.MeshPhysicalMaterial;
  dispose: () => void;
}

const clampInt = (v: number, lo: number, hi: number, dflt: number): number => {
  const n = Math.round(Number.isFinite(v) ? v : dflt);
  return Math.max(lo, Math.min(hi, n));
};

function getTentaclePosition(
  b: TentacleBase,
  t: number,
  time: number,
  waveSpeed: number,
  curlFreq: number,
  amp: number,
  microPhase: number,
  out: THREE.Vector3,
): void {
  const totalHeight = CAPSULE_LENGTH + CAPSULE_RADIUS * 2;
  const startY = -totalHeight / 2 + 0.5;
  const endY = totalHeight / 2 - 0.5;
  const y = startY + t * (endY - startY);

  const activePhase = microPhase * Math.min(t * 5.0, 1.0);
  const timePhase = (time + activePhase) * waveSpeed;
  const curl = y * curlFreq;
  const yEnvelope = Math.sin(t * Math.PI);

  const xOffset =
    Math.sin(curl + timePhase + b.phaseX) * amp +
    Math.sin(curl * 0.5 - timePhase * 0.7 + b.phaseY) * (amp * 0.5);
  const zOffset =
    Math.cos(curl * 0.8 + timePhase * 1.1 + b.phaseZ) * amp +
    Math.cos(curl * 0.4 - timePhase * 0.5 + b.phaseX) * (amp * 0.5);

  out.set((b.baseX + xOffset) * yEnvelope, y, (b.baseZ + zOffset) * yEnvelope);
}

function buildEdison(tentacleCount: number, filaments: number, particleCount: number): EdisonScene {
  const objects: THREE.Object3D[] = [];

  const coreMat = new THREE.LineBasicMaterial({
    transparent: true, opacity: 0.4, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false,
  });
  const glowMat = new THREE.LineBasicMaterial({
    transparent: true, opacity: 0.06, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false,
  });
  const particleMat = new THREE.PointsMaterial({
    size: 0.05, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending,
    depthWrite: false, sizeAttenuation: true, toneMapped: false,
  });
  const capsuleMat = new THREE.MeshPhysicalMaterial({
    transparent: true, opacity: 0.05, roughness: 0.15, metalness: 1.0, clearcoat: 1.0,
    clearcoatRoughness: 0.15, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
  });

  // Glass capsule envelope.
  const capsuleGeo = new THREE.CapsuleGeometry(CAPSULE_RADIUS, CAPSULE_LENGTH, 12, 24);
  const capsule = new THREE.Mesh(capsuleGeo, capsuleMat);
  objects.push(capsule);

  // Tentacle bundles + their filament strands.
  const bases: TentacleBase[] = [];
  const lineData: LineDatum[] = [];
  const lineGeos: THREE.BufferGeometry[] = [];
  const spread = 0.62;

  const addStrand = (tentacleIndex: number, strandSpread: number, material: THREE.LineBasicMaterial) => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(POINTS_PER_LINE * 3), 3));
    lineGeos.push(geo);
    const line = new THREE.Line(geo, material);
    objects.push(line);
    lineData.push({
      line,
      tentacleIndex,
      localOffsetX: (Math.random() - 0.5) * strandSpread,
      localOffsetZ: (Math.random() - 0.5) * strandSpread,
      microPhase: Math.random() * 0.5,
    });
  };

  for (let r = 0; r < tentacleCount; r++) {
    bases.push({
      baseX: (Math.random() - 0.5) * 4.0,
      baseZ: (Math.random() - 0.5) * 4.0,
      phaseX: Math.random() * Math.PI * 2,
      phaseY: Math.random() * Math.PI * 2,
      phaseZ: Math.random() * Math.PI * 2,
    });
    for (let c = 0; c < CORE_LINES; c++) addStrand(r, 0.2, coreMat);
    for (let g = 0; g < filaments; g++) addStrand(r, spread, glowMat);
  }

  // Particles flowing along the filaments.
  const particleData: ParticleDatum[] = [];
  for (let i = 0; i < particleCount; i++) {
    particleData.push({
      tentacleIndex: Math.floor(Math.random() * tentacleCount),
      t: Math.random(),
      speed: (Math.random() * 0.8 + 0.2) * 0.002,
      angle: Math.random() * Math.PI * 2,
      radiusBase: Math.random(),
      orbitSpeed: (Math.random() - 0.5) * 2.0,
    });
  }
  const particleGeo = new THREE.BufferGeometry();
  particleGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(particleCount * 3), 3));
  const particles = new THREE.Points(particleGeo, particleMat);
  objects.push(particles);

  return {
    objects, bases, lineData, particles, particleData,
    coreMat, glowMat, particleMat, capsuleMat,
    dispose: () => {
      capsuleGeo.dispose();
      particleGeo.dispose();
      for (const g of lineGeos) g.dispose();
      coreMat.dispose(); glowMat.dispose(); particleMat.dispose(); capsuleMat.dispose();
    },
  };
}

export function EdisonVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { gl, camera } = useThree();
  const { base, effectiveState } = useComposedBase<EdisonPreset>(voiceState);
  const { dBotRef, dUserRef } = useAudioEnvelopes({ botStream, localStream });
  const { snapRef } = useStateCrossfade(effectiveState, base);
  const { breathNormRef } = useIdleBreath();
  const rayTargetRef = useRef<THREE.Mesh>(null);
  const { dragVelRef } = usePointerInteraction(rayTargetRef);
  const hostRef = useRef<THREE.Group>(null);

  void dUserRef;

  const tentacleCount = clampInt(base.tentacleCount, 3, 14, 8);
  const filaments = clampInt(base.filaments, 4, 24, 12);
  const particleCount = clampInt(base.particleCount, 200, 3000, 1800);

  useEffect(() => { gl.setPixelRatio(base.dpr); }, [base.dpr, gl]);
  useEffect(() => { camera.position.set(0, 0, 13); }, [camera]);

  const scene = useMemo(
    () => buildEdison(tentacleCount, filaments, particleCount),
    [tentacleCount, filaments, particleCount],
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    for (const o of scene.objects) host.add(o);
    return () => {
      for (const o of scene.objects) host.remove(o);
      scene.dispose();
    };
  }, [scene]);

  const tmp = useMemo(() => new THREE.Vector3(), []);

  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const host = hostRef.current;
    const snap = snapRef.current;
    if (!host || !snap) return;

    const dBot = dBotRef.current;
    const breath = breathNormRef.current;
    const t = performance.now() * 0.001;

    // Host scale (orb framing × state scale × breath) + rotation.
    host.scale.setScalar(ORB_SCALE * snap.scale * (1 + breath * BREATH_AMP));
    const spin = snap.rotation * ROTATION_SCALE * base.speed;
    host.rotation.y += delta * spin + dragVelRef.current.y * delta;
    if (host.rotation.y > ROT_WRAP) host.rotation.y -= ROT_WRAP;
    if (host.rotation.y < -ROT_WRAP) host.rotation.y += ROT_WRAP;

    // State-reactive colours + glass/particle look.
    scene.coreMat.color.copy(snap.primary);
    scene.glowMat.color.copy(snap.secondary);
    scene.particleMat.color.copy(snap.primary);
    scene.particleMat.size = base.particleSize;
    scene.capsuleMat.color.copy(snap.secondary);
    scene.capsuleMat.opacity = base.capsuleOpacity;
    scene.coreMat.opacity = 0.4 + dBot * 0.3;

    // Energy: bot voice boosts writhe + flow.
    const amp = base.writhingAmplitude * (1 + dBot * 0.6);
    const waveSpeed = base.waveSpeed * snap.speed * (1 + dBot * 0.3);
    const flow = base.particleSpeed * (1 + dBot * 0.5);

    // Writhe the filament strands.
    for (const d of scene.lineData) {
      const arr = d.line.geometry.attributes.position.array as Float32Array;
      for (let i = 0; i < POINTS_PER_LINE; i++) {
        const tt = i / (POINTS_PER_LINE - 1);
        getTentaclePosition(scene.bases[d.tentacleIndex], tt, t, waveSpeed, base.curlFrequency, amp, d.microPhase, tmp);
        const bottom = Math.min((tt * TAPER) ** 2, 1);
        const top = Math.min(((1 - tt) * TAPER) ** 2, 1);
        const gt = bottom * top;
        arr[i * 3] = tmp.x + d.localOffsetX * gt;
        arr[i * 3 + 1] = tmp.y;
        arr[i * 3 + 2] = tmp.z + d.localOffsetZ * gt;
      }
      d.line.geometry.attributes.position.needsUpdate = true;
    }

    // Stream the particles along the filaments (downward, looping).
    const pArr = scene.particles.geometry.attributes.position.array as Float32Array;
    for (let i = 0; i < particleCount; i++) {
      const pd = scene.particleData[i];
      pd.t -= pd.speed * flow;
      if (pd.t < 0) {
        pd.t = 1;
        pd.tentacleIndex = Math.floor(Math.random() * tentacleCount);
      }
      getTentaclePosition(scene.bases[pd.tentacleIndex], pd.t, t, waveSpeed, base.curlFrequency, amp, 0, tmp);
      pd.angle += pd.orbitSpeed * 0.02;
      const bottom = Math.min((pd.t * TAPER) ** 2, 1);
      const top = Math.min(((1 - pd.t) * TAPER) ** 2, 1);
      const yEnv = Math.sin(pd.t * Math.PI);
      const radius = pd.radiusBase * base.particleSpread * yEnv * bottom * top * CAPSULE_RADIUS;
      pArr[i * 3] = tmp.x + Math.sin(pd.angle) * radius;
      pArr[i * 3 + 1] = tmp.y;
      pArr[i * 3 + 2] = tmp.z + Math.cos(pd.angle) * radius;
    }
    scene.particles.geometry.attributes.position.needsUpdate = true;
  });

  return (
    <group ref={hostRef}>
      {/* Invisible raycast target (in host space) so drag-spin / click-pulse land. */}
      <mesh ref={rayTargetRef} visible={false}>
        <sphereGeometry args={[CAPSULE_RADIUS, 16, 16]} />
      </mesh>
    </group>
  );
}
