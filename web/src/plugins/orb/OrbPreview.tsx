import { Canvas } from '@react-three/fiber';
import { EffectComposer } from '@react-three/postprocessing';
import { useEffect, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { useActiveVariant, useOrbState } from './useOrbState';
import { useVoiceStateSelector } from '@/voice/hooks';
import { LumaChromaticAberrationEffect } from './shared/chromaticAberration';
import type { FractalPreset } from './variants/fractal/presets';

/**
 * Secondary orb canvas for the mobile drawer. Mirrors the main orb's
 * state via the orbStore (every variant reads from it). No audio pipe
 * — this is a settings preview, not an audio-reactive clone.
 */
export function OrbPreview() {
  const variant = useActiveVariant();
  const voiceState = useVoiceStateSelector((s) => s.state);

  if (!variant) return null;
  const Variant = variant.Component;

  return (
    <Canvas
      camera={{ fov: 45, near: 0.1, far: 100, position: [0, 0, 13] }}
      dpr={0.7}
      gl={{ antialias: true, alpha: false }}
      className="absolute inset-0"
    >
      <color attach="background" args={['#000000']} />
      <Variant voiceState={voiceState} botStream={null} localStream={null} />
      <EffectComposer enabled>
        <CADriver />
      </EffectComposer>
    </Canvas>
  );
}

function CADriver() {
  const { params } = useOrbState();
  const base = params as unknown as FractalPreset;
  const effect = useMemo(() => new LumaChromaticAberrationEffect({ amount: 0.025 }), []);
  useFrame(() => {
    const target = Math.min(0.05, (base.chromaticAberration ?? 0.022) * 0.9);
    effect.setAmount(target);
  });
  useEffect(() => () => effect.dispose(), [effect]);
  return <primitive object={effect} />;
}
