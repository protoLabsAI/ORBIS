import { useMemo, useEffect } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { EffectComposer } from '@react-three/postprocessing';
import {
  usePipecatClient,
  usePipecatClientMediaTrack,
  usePipecatClientTransportState,
} from '@pipecat-ai/client-react';
import { useActiveVariant } from './useOrbState';
import { useVoiceStateSelector } from '@/voice/hooks';
import { LumaChromaticAberrationEffect } from './shared/chromaticAberration';
import { useOrbState } from './useOrbState';
import type { FractalPreset } from './variants/fractal/presets';
// Side-effect imports — variants register themselves on import.
import './variants';

/**
 * Primary orb canvas. Hosts a Canvas + whichever variant is active.
 * Also owns double-click-to-connect: on mouse, dblclick triggers
 * client.connect(); on touch, a single tap does.
 */
export function OrbStage() {
  const variant = useActiveVariant();
  const voiceState = useVoiceStateSelector((s) => s.state);
  const botTrack = usePipecatClientMediaTrack('audio', 'bot');
  const localTrack = usePipecatClientMediaTrack('audio', 'local');
  const client = usePipecatClient();
  const transport = usePipecatClientTransportState();

  const botStream = useMemo(
    () => (botTrack ? new MediaStream([botTrack]) : null),
    [botTrack],
  );
  const localStream = useMemo(
    () => (localTrack ? new MediaStream([localTrack]) : null),
    [localTrack],
  );

  // Double-click / double-tap toggles the voice session on both mouse
  // and touch. Single-tap stays free for drag-spin / click-pulse so
  // users can play with the orb without accidentally (dis)connecting.
  const onDoubleClick = () => {
    if (!client) return;
    const disconnected =
      transport === 'disconnected' ||
      transport === 'initialized' ||
      transport === 'error';
    const active =
      transport === 'ready' ||
      transport === 'connected' ||
      transport === 'connecting' ||
      transport === 'authenticating';
    if (disconnected) {
      client.connect().catch((err) => console.error('[orb] connect error:', err));
    } else if (active) {
      client.disconnect().catch((err) => console.error('[orb] disconnect error:', err));
    }
  };

  if (!variant) {
    return (
      <div className="absolute inset-0 grid place-items-center text-zinc-500 text-sm">
        No orb variant registered.
      </div>
    );
  }

  const Variant = variant.Component;

  return (
    <div
      onDoubleClick={onDoubleClick}
      className="absolute inset-0"
      style={{ touchAction: 'none' }}
    >
      <Canvas
        camera={{ fov: 45, near: 0.1, far: 100, position: [0, 0, 13] }}
        dpr={0.7}
        gl={{ antialias: true, alpha: false }}
      >
        <color attach="background" args={['#000000']} />
        <Variant voiceState={voiceState} botStream={botStream} localStream={localStream} />
        <EffectComposer enabled>
          <CADriver />
        </EffectComposer>
      </Canvas>
    </div>
  );
}

/**
 * Applies the current CA amount to the luma-masked effect every frame.
 * Lives inside `<EffectComposer>` so the effect primitive is mounted
 * in the right place.
 */
function CADriver() {
  const { params } = useOrbState();
  const base = params as unknown as FractalPreset;
  const effect = useMemo(() => new LumaChromaticAberrationEffect({ amount: 0.025 }), []);

  useFrame(() => {
    // State-modulated value is derived the same way as in the variant's
    // driver. The variant doesn't own the CA pass (post is above the
    // variant in the composer), so we read the base directly. Close
    // enough — audio-driven spike is small (~0.008).
    const target = Math.min(0.05, (base.chromaticAberration ?? 0.022) * 0.9);
    effect.setAmount(target);
  });

  useEffect(() => () => effect.dispose(), [effect]);

  return <primitive object={effect} />;
}
