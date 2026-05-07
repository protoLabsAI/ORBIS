/**
 * Capture a fixed-duration mono WAV recording from getUserMedia.
 *
 * MediaRecorder produces WebM/Opus on Chrome and audio/mp4 on Safari —
 * the backend voiceprint encoder uses libsndfile (via soundfile) which
 * handles WAV but not WebM. So we capture raw PCM via WebAudio and
 * encode WAV ourselves. ScriptProcessorNode is deprecated but
 * universally supported and the simplest path; AudioWorklet would be
 * cleaner but adds a separate worklet file for ~10s of capture.
 *
 * Usage:
 *   const ctrl = await startWavRecorder({ deviceId, sampleRate: 16000 });
 *   ctrl.onLevel = rms => setLevel(rms);
 *   const wav = await ctrl.stopAfter(10_000);  // returns a Blob
 *
 * The recorder always tears the stream down on stop or error — callers
 * don't need to manage track lifetimes.
 */

export interface RecorderOptions {
  /** Preferred input device (from enumerateDevices). undefined = system default. */
  deviceId?: string;
  /**
   * Target sample rate of the WAV output. Whatever AudioContext gives
   * us (browser default, usually 48k) is downsampled to this rate via
   * naive linear interpolation before encoding. ECAPA expects 16k —
   * we resample server-side too, but doing it here keeps the network
   * payload small.
   */
  sampleRate: number;
}

export interface RecorderHandle {
  /** Live RMS over the last 50ms window (0…1). Set the callback BEFORE calling stopAfter. */
  onLevel?: (rms: number) => void;
  /** Stop after the given time, return the encoded WAV Blob. */
  stopAfter: (durationMs: number) => Promise<Blob>;
  /** Stop now (used on cancel / error). Returns whatever's been buffered. */
  stop: () => Promise<Blob>;
}

export async function startWavRecorder(options: RecorderOptions): Promise<RecorderHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: options.deviceId ? { deviceId: { exact: options.deviceId } } : true,
  });

  const ctx = new AudioContext();
  const source = ctx.createMediaStreamSource(stream);

  // ScriptProcessorNode buffer size: 4096 samples gives ~85ms granularity
  // at 48kHz, which is fine for a 10s recording.
  const buffer = 4096;
  const processor = ctx.createScriptProcessor(buffer, 1, 1);
  source.connect(processor);
  processor.connect(ctx.destination);

  const inputSampleRate = ctx.sampleRate;
  const outputSampleRate = options.sampleRate;
  const chunks: Float32Array[] = [];

  const handle: RecorderHandle = {
    stopAfter: () => Promise.resolve(new Blob()),
    stop: () => Promise.resolve(new Blob()),
  };

  let stopped = false;
  let resolveDone: ((b: Blob) => void) | null = null;

  const cleanup = () => {
    if (stopped) return;
    stopped = true;
    try { processor.disconnect(); } catch (e) { /* already torn down */ void e; }
    try { source.disconnect(); } catch (e) { void e; }
    try { for (const t of stream.getTracks()) t.stop(); } catch (e) { void e; }
    if (ctx.state !== 'closed') void ctx.close();
  };

  const finalize = (): Blob => {
    cleanup();
    if (chunks.length === 0) return new Blob();
    const total = chunks.reduce((sum, c) => sum + c.length, 0);
    const merged = new Float32Array(total);
    let pos = 0;
    for (const c of chunks) {
      merged.set(c, pos);
      pos += c.length;
    }
    const downsampled = downsample(merged, inputSampleRate, outputSampleRate);
    return encodeWav(downsampled, outputSampleRate);
  };

  processor.onaudioprocess = (ev) => {
    if (stopped) return;
    const input = ev.inputBuffer.getChannelData(0);
    // Copy because the underlying buffer is reused by the audio thread.
    chunks.push(new Float32Array(input));

    if (handle.onLevel) {
      // Quick RMS over the chunk for the level meter.
      let sum = 0;
      for (let i = 0; i < input.length; i += 1) {
        sum += input[i] * input[i];
      }
      handle.onLevel(Math.sqrt(sum / input.length));
    }
  };

  handle.stopAfter = (durationMs: number) => {
    return new Promise<Blob>((resolve) => {
      resolveDone = resolve;
      window.setTimeout(() => {
        if (resolveDone) {
          resolveDone(finalize());
          resolveDone = null;
        }
      }, durationMs);
    });
  };

  handle.stop = () => {
    if (resolveDone) {
      const blob = finalize();
      resolveDone(blob);
      resolveDone = null;
      return Promise.resolve(blob);
    }
    return Promise.resolve(finalize());
  };

  return handle;
}

/**
 * Naive linear-interpolation downsampler. Good enough for speaker
 * embedding (which doesn't care about the high-frequency anti-aliasing
 * a polyphase filter would give us) and ~free vs pulling in a DSP lib.
 */
function downsample(
  input: Float32Array,
  inputRate: number,
  outputRate: number,
): Float32Array {
  if (inputRate === outputRate) return input;
  const ratio = inputRate / outputRate;
  const outputLength = Math.floor(input.length / ratio);
  const out = new Float32Array(outputLength);
  for (let i = 0; i < outputLength; i += 1) {
    const srcIdx = i * ratio;
    const lo = Math.floor(srcIdx);
    const hi = Math.min(lo + 1, input.length - 1);
    const frac = srcIdx - lo;
    out[i] = input[lo] * (1 - frac) + input[hi] * frac;
  }
  return out;
}

/**
 * Encode a Float32Array of mono PCM samples ([-1, +1]) as 16-bit PCM
 * WAV. The format is the standard Microsoft RIFF layout —
 * 44-byte header + interleaved samples.
 */
function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const numFrames = samples.length;
  const bytesPerSample = 2;
  const numChannels = 1;
  const byteRate = sampleRate * numChannels * bytesPerSample;
  const blockAlign = numChannels * bytesPerSample;
  const dataSize = numFrames * blockAlign;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  // RIFF header
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeString(view, 8, 'WAVE');

  // fmt sub-chunk
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);              // sub-chunk size (PCM = 16)
  view.setUint16(20, 1, true);               // audio format (PCM = 1)
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bytesPerSample * 8, true); // bits per sample

  // data sub-chunk
  writeString(view, 36, 'data');
  view.setUint32(40, dataSize, true);

  // Convert and write samples.
  let offset = 44;
  for (let i = 0; i < numFrames; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    offset += 2;
  }

  return new Blob([buffer], { type: 'audio/wav' });
}

function writeString(view: DataView, offset: number, str: string): void {
  for (let i = 0; i < str.length; i += 1) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}
