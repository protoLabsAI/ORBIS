/**
 * Browser audio I/O for the voice loop.
 *
 * Capture: getUserMedia → 16 kHz mono Float32 PCM (what Moonshine/Whisper
 * want), with an AnalyserNode feeding levels.mic so the orb pulses while
 * you talk. Playback: Kokoro's Float32 → an AudioBuffer with an
 * AnalyserNode feeding levels.playback so the orb pulses while it speaks.
 */
import { setMic, setPlayback } from './levels';
import { deviceStore } from './devices';

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

export async function startCapture(): Promise<void> {
  const inputId = deviceStore.get().inputId;
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      ...(inputId ? { deviceId: { exact: inputId } } : {}),
    },
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
  const tick = () => {
    analyser.getFloatTimeDomainData(buf);
    setMic(rms(buf));
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
    const outputId = deviceStore.get().outputId;
    const sink = ctx as AudioContext & { setSinkId?: (id: string) => Promise<void> };
    if (outputId && sink.setSinkId) void sink.setSinkId(outputId).catch(() => {});
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
