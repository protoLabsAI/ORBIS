import { useEffect, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { invoke } from '@tauri-apps/api/core';
import { Envelope, rmsFromAnalyser } from '../envelope';
import { lerp } from '../math';
import { DISP_ALPHA, ENV_BOT, ENV_USER } from '../constants';

// Gain applied to the native (Rust) RMS levels before the envelope
// follower. The raw RMS is small (~0.05–0.2 for TTS, ~0.03–0.1 for
// speech), so lift it into a punchy [0, 1] reactive range.
const NATIVE_BOT_GAIN = 4.0;
const NATIVE_USER_GAIN = 6.0;

type Bundle = { analyser: AnalyserNode; source: MediaStreamAudioSourceNode; buf: Uint8Array };

/**
 * Subscribes to the bot + user media streams, builds an AudioContext
 * lazily, runs an asymmetric two-stage envelope follower per source,
 * and exposes normalized [0, 1] levels as refs.
 *
 * Updated every frame via useFrame — reads are ref.current at the
 * caller's own useFrame. Re-renders cost zero here.
 */
export function useAudioEnvelopes({
  botStream,
  localStream,
}: {
  botStream?: MediaStream | null;
  localStream?: MediaStream | null;
}) {
  const dBotRef = useRef(0);
  const dUserRef = useRef(0);

  // Native audio levels (Tauri). The native app has no browser
  // MediaStreams, so poll the Rust engine for mic + TTS-playback levels
  // and feed them through the same envelope followers below — the orb
  // pulses to her voice while she speaks and to yours while listening.
  const nativeRef = useRef({ mic: 0, playback: 0 });
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      timer = null;
      // Don't burn an IPC round-trip ~30×/s when the window is hidden
      // (mini-orb / minimized) — the orb isn't being rendered then, so
      // the levels aren't read. Resume on visibilitychange below.
      if (!active || document.hidden) return;
      try {
        const lv = await invoke<{ mic: number; playback: number }>('get_audio_levels');
        if (lv) nativeRef.current = lv;
      } catch {
        // Command unavailable (plain browser dev) — leave at 0.
      }
      if (active && !document.hidden) timer = setTimeout(poll, 33); // ~30 Hz
    };
    const onVisibility = () => {
      // Becoming visible with no poll scheduled — kick it back off.
      if (!document.hidden && active && timer === null) poll();
    };
    document.addEventListener('visibilitychange', onVisibility);
    poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, []);

  const stateRef = useRef<{
    ctx: AudioContext | null;
    bot: Bundle | null;
    user: Bundle | null;
    envBot: Envelope;
    envUser: Envelope;
  }>({
    ctx: null,
    bot: null,
    user: null,
    envBot: new Envelope(ENV_BOT),
    envUser: new Envelope(ENV_USER),
  });

  useEffect(() => {
    const s = stateRef.current;
    if (!botStream) return;
    s.bot = attachStream(s, botStream);
    return () => {
      try { s.bot?.source.disconnect(); } catch {}
      s.bot = null;
      s.envBot.reset();
    };
  }, [botStream]);

  useEffect(() => {
    const s = stateRef.current;
    if (!localStream) return;
    s.user = attachStream(s, localStream);
    return () => {
      try { s.user?.source.disconnect(); } catch {}
      s.user = null;
      s.envUser.reset();
    };
  }, [localStream]);

  useEffect(() => {
    return () => {
      const s = stateRef.current;
      if (s.ctx) { try { s.ctx.close(); } catch {} }
      s.ctx = null;
    };
  }, []);

  useFrame(() => {
    const s = stateRef.current;
    const nat = nativeRef.current;
    let bot = 0, user = 0;
    if (s.bot) {
      bot = s.envBot.update(rmsFromAnalyser(s.bot.analyser, s.bot.buf));
    } else {
      // Native fallback: her live TTS level, gained into a punchy range.
      bot = s.envBot.update(Math.min(1, nat.playback * NATIVE_BOT_GAIN));
    }
    if (s.user) {
      user = s.envUser.update(rmsFromAnalyser(s.user.analyser, s.user.buf));
    } else {
      user = s.envUser.update(Math.min(1, nat.mic * NATIVE_USER_GAIN));
    }
    dBotRef.current = lerp(dBotRef.current, bot, DISP_ALPHA);
    dUserRef.current = lerp(dUserRef.current, user, DISP_ALPHA);
  });

  return { dBotRef, dUserRef };
}

function attachStream(
  s: { ctx: AudioContext | null },
  stream: MediaStream,
): Bundle | null {
  if (!s.ctx) {
    try {
      const Ctx: typeof AudioContext =
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (window.AudioContext ?? (window as any).webkitAudioContext);
      s.ctx = new Ctx();
    } catch {
      return null;
    }
  }
  const ctx = s.ctx!;
  if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.55;
  source.connect(analyser);
  const buf = new Uint8Array(analyser.fftSize);
  return { analyser, source, buf };
}
