import { useMemo, useEffect, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { EffectComposer } from '@react-three/postprocessing';
import { useActiveVariant } from './useOrbState';
import { useVoiceStateSelector } from '@/voice/hooks';
import { LumaChromaticAberrationEffect } from './shared/chromaticAberration';
import { useOrbState } from './useOrbState';
import { pushStatusTransient } from '@/sdk';
import { invoke } from '@tauri-apps/api/core';
import { voiceStore } from '@/voice/state';
import type { FractalPreset } from './variants/fractal/presets';
// Side-effect imports — variants register themselves on import.
import './variants';

/**
 * Primary orb canvas. Hosts a Canvas + whichever variant is active.
 *
 * Pre-2026-04-28 this also owned double-click-to-connect for the
 * WebRTC path and read MediaStreams from PipecatClient for the
 * audio-reactive shaders. The web/PWA path was dropped (DECISIONS.md
 * amendment of that date), audio runs entirely in the Rust CPAL
 * engine, and the orb no longer has access to live waveform data.
 * Audio reactivity will return in a later phase via a sidecar-pushed
 * envelope feed.
 */
export function OrbStage() {
  const variant = useActiveVariant();
  const voiceState = useVoiceStateSelector((s) => s.state);

  const botStream = useMemo<MediaStream | null>(() => null, []);
  const localStream = useMemo<MediaStream | null>(() => null, []);

  // Pause the raymarch render loop while the window is hidden (mini-orb /
  // minimized / occluded). The orb is a full-screen GLSL raymarch; left on
  // `frameloop="always"` it keeps shading at full rate off-screen and drains
  // battery. WKWebView throttles rAF inconsistently for off-screen windows,
  // so gate it explicitly. See audit L3.
  const [frameloop, setFrameloop] = useState<'always' | 'never'>(
    typeof document !== 'undefined' && document.hidden ? 'never' : 'always',
  );
  useEffect(() => {
    const onVisibility = () => setFrameloop(document.hidden ? 'never' : 'always');
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  // Double-click / double-tap starts (or ends) a push-to-talk listening turn.
  // Hard mute is king: if the mic button has muted the mic, double-click is a
  // no-op (tap the mic button to unmute first) — the Rust engine gates the wake
  // word the same way, so muted is truly silent.
  const onDoubleClick = () => {
    if (voiceStore.getSnapshot().micMuted) {
      pushStatusTransient('muted — tap the mic to unmute', 2400);
      return;
    }
    const next = !voiceStore.getSnapshot().micListening;
    voiceStore.update({ micListening: next });
    pushStatusTransient(next ? 'listening…' : 'stopped', 1800);
    invoke('set_mic_listening', { on: next }).catch(() => {
      // Command unavailable (e.g. non-native dev build) — keep local UI state.
    });
  };

  if (!variant) {
    return (
      <div className="absolute inset-0 grid place-items-center text-fg-subtle text-sm">
        No orb variant registered.
      </div>
    );
  }

  const Variant = variant.Component;
  const Post = variant.PostEffects;

  return (
    <div
      onDoubleClick={onDoubleClick}
      className="absolute inset-0"
      style={{ touchAction: 'none' }}
    >
      <Canvas
        frameloop={frameloop}
        camera={{ fov: 45, near: 0.1, far: 100, position: [0, 0, 13] }}
        dpr={0.7}
        gl={{ antialias: true, alpha: false }}
      >
        <color attach="background" args={['#000000']} />
        <Variant voiceState={voiceState} botStream={botStream} localStream={localStream} />
        <EffectComposer enabled>
          <CADriver />
          {Post ? <Post /> : <></>}
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
