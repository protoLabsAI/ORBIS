import { useEffect, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Envelope, rmsFromAnalyser } from '../envelope';
import { lerp } from '../math';
import { DISP_ALPHA, ENV_BOT, ENV_USER } from '../constants';

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
    let bot = 0, user = 0;
    if (s.bot) {
      const raw = rmsFromAnalyser(s.bot.analyser, s.bot.buf);
      bot = s.envBot.update(raw);
    }
    if (s.user) {
      const raw = rmsFromAnalyser(s.user.analyser, s.user.buf);
      user = s.envUser.update(raw);
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
