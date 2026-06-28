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
      onEndpoint();
    }
    cap!.raf = requestAnimationFrame(tick);
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

// ---- Playback (Kokoro Float32 → speakers + levels.playback) ----
let playCtx: AudioContext | null = null;

export function playPCM(pcm: Float32Array, rate: number): Promise<void> {
  return new Promise((resolve) => {
    if (!playCtx) playCtx = new AudioContext();
    const ctx = playCtx;
    if (ctx.state === 'suspended') void ctx.resume();
    const buffer = ctx.createBuffer(1, pcm.length, rate);
    buffer.getChannelData(0).set(pcm);
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    src.connect(analyser);
    analyser.connect(ctx.destination);
    const buf = new Float32Array(analyser.fftSize);
    let raf = 0;
    const tick = () => {
      analyser.getFloatTimeDomainData(buf);
      setPlayback(rms(buf));
      raf = requestAnimationFrame(tick);
    };
    src.onended = () => {
      cancelAnimationFrame(raf);
      setPlayback(0);
      resolve();
    };
    src.start();
    tick();
  });
}
