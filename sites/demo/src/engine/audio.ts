/**
 * Browser audio I/O for the voice loop.
 *
 * Capture: getUserMedia → 16 kHz mono Float32 PCM (what Moonshine/Whisper
 * want), with an AnalyserNode feeding levels.mic so the orb pulses while
 * you talk. Playback: Kokoro's Float32 → an AudioBuffer with an
 * AnalyserNode feeding levels.playback so the orb pulses while it speaks.
 */
import { setMic, setPlayback } from './levels';

function rms(buf: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / buf.length);
}

// ---- Mic capture (16 kHz mono Float32) ----
interface Capture {
  ctx: AudioContext;
  stream: MediaStream;
  node: ScriptProcessorNode;
  analyser: AnalyserNode;
  chunks: Float32Array[];
  raf: number;
}
let cap: Capture | null = null;

/** Request mic permission within a user gesture (then release). Lets us
 *  open the mic later, post-model-load, without a fresh gesture. */
export async function primeMicPermission(): Promise<boolean> {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}

export async function startCapture(onEndpoint?: () => void): Promise<void> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const ctx = new AudioContext({ sampleRate: 16000 });
  if (ctx.state === 'suspended') await ctx.resume();
  const src = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  src.connect(analyser);
  const node = ctx.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  node.onaudioprocess = (e) => {
    chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  };
  src.connect(node);
  node.connect(ctx.destination); // SP only fires when connected; output is silent
  const buf = new Float32Array(analyser.fftSize);
  // Voice-activity endpointing: once you've spoken, a ~1.3s pause (or a 20s
  // hard cap) auto-stops capture so it replies without a second tap.
  const SPEECH_RMS = 0.015;
  const SILENCE_HANG_MS = 1300;
  const MAX_MS = 20000;
  const startedAt = performance.now();
  let spoke = false;
  let lastVoiceAt = startedAt;
  let fired = false;
  const tick = () => {
    if (!cap) return; // capture stopped — end the loop
    analyser.getFloatTimeDomainData(buf);
    const level = rms(buf);
    setMic(level);
    const now = performance.now();
    if (level > SPEECH_RMS) {
      spoke = true;
      lastVoiceAt = now;
    }
    if (
      !fired &&
      onEndpoint &&
      ((spoke && now - lastVoiceAt > SILENCE_HANG_MS) || now - startedAt > MAX_MS)
    ) {
      fired = true;
      onEndpoint(); // may synchronously stop capture (nulls cap)
    }
    if (cap) cap.raf = requestAnimationFrame(tick); // don't reschedule after stop
  };
  cap = { ctx, stream, node, analyser, chunks, raf: 0 };
  tick();
}

/** Stop capture; return the recorded mono Float32 PCM at 16 kHz. */
export async function stopCapture(): Promise<Float32Array> {
  if (!cap) return new Float32Array(0);
  const c = cap;
  cap = null;
  cancelAnimationFrame(c.raf);
  setMic(0);
  try {
    c.node.disconnect();
    c.analyser.disconnect();
  } catch {
    /* noop */
  }
  c.stream.getTracks().forEach((t) => t.stop());
  const total = c.chunks.reduce((n, ch) => n + ch.length, 0);
  const out = new Float32Array(total);
  let o = 0;
  for (const ch of c.chunks) {
    out.set(ch, o);
    o += ch.length;
  }
  try {
    await c.ctx.close();
  } catch {
    /* noop */
  }
  return out;
}

// ---- Streaming playback (Kokoro sentence chunks → gapless speakers) ----
// Chunks arrive while earlier ones are still playing, so each is scheduled
// back-to-back on a shared AudioContext; one AnalyserNode drives
// levels.playback so the orb pulses for the whole utterance.
let playCtx: AudioContext | null = null;
let playAnalyser: AnalyserNode | null = null;
let nextStartAt = 0;
let active = 0; // scheduled-but-not-ended sources
let closed = true; // no more chunks coming
let drainResolve: (() => void) | null = null;
let levelRaf = 0;

function ensurePlayCtx(): AudioContext {
  if (!playCtx) {
    playCtx = new AudioContext();
    playAnalyser = playCtx.createAnalyser();
    playAnalyser.fftSize = 1024;
    playAnalyser.connect(playCtx.destination);
  }
  return playCtx;
}

function levelLoop(): void {
  const a = playAnalyser;
  if (!a) return;
  const b = new Float32Array(a.fftSize);
  cancelAnimationFrame(levelRaf);
  const tick = () => {
    a.getFloatTimeDomainData(b);
    setPlayback(rms(b));
    levelRaf = requestAnimationFrame(tick);
  };
  tick();
}

function finishDrain(): void {
  cancelAnimationFrame(levelRaf);
  setPlayback(0);
  const r = drainResolve;
  drainResolve = null;
  r?.();
}

/** Open a playback session; the returned promise resolves once every
 *  enqueued chunk has finished AND endPlayback() has been called. */
export function beginPlayback(): Promise<void> {
  const ctx = ensurePlayCtx();
  if (ctx.state === 'suspended') void ctx.resume();
  nextStartAt = ctx.currentTime;
  active = 0;
  closed = false;
  levelLoop();
  return new Promise((resolve) => {
    drainResolve = resolve;
  });
}

/** Queue one synthesized chunk, scheduled right after the previous one. */
export function enqueuePlayback(pcm: Float32Array, rate: number): void {
  const ctx = playCtx;
  const a = playAnalyser;
  if (!ctx || !a) return;
  const buffer = ctx.createBuffer(1, pcm.length, rate);
  buffer.getChannelData(0).set(pcm);
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  src.connect(a);
  const startAt = Math.max(ctx.currentTime, nextStartAt);
  src.start(startAt);
  nextStartAt = startAt + buffer.duration;
  active++;
  src.onended = () => {
    active--;
    if (active === 0 && closed) finishDrain();
  };
}

/** No more chunks coming; the session drains once playback catches up. */
export function endPlayback(): void {
  closed = true;
  if (active === 0) finishDrain();
}
