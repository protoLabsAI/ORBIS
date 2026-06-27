/// <reference lib="webworker" />
/**
 * Speech worker — on-device STT (Moonshine) + TTS (Kokoro) on WebGPU.
 *
 * Kept off the main thread so model load + inference never stall the orb.
 * Audio playback itself happens on the main thread (Web Audio); this worker
 * just returns the synthesized Float32 PCM.
 *
 * Protocol (main → worker): 'load' | { transcribe, audio } | { synthesize, text, voice }
 * Protocol (worker → main): 'progress' | 'ready' | 'transcript' | 'audio' | 'error'
 */
import { pipeline, env, type AutomaticSpeechRecognitionPipeline } from '@huggingface/transformers';
import { KokoroTTS } from 'kokoro-js';

// Cache model files (Moonshine here; Kokoro's bundled transformers caches
// by default too) so a download happens once.
env.useBrowserCache = true;
env.allowLocalModels = false;

const STT_MODEL = 'onnx-community/moonshine-base-ONNX';
const TTS_MODEL = 'onnx-community/Kokoro-82M-v1.0-ONNX';

let asr: AutomaticSpeechRecognitionPipeline | null = null;
let tts: KokoroTTS | null = null;

const post = (msg: Record<string, unknown>, transfer?: Transferable[]) =>
  (self as unknown as Worker).postMessage(msg, transfer ?? []);

async function load(): Promise<void> {
  const progress_callback = (p: unknown) => post({ type: 'progress', data: p });
  asr ??= (await pipeline('automatic-speech-recognition', STT_MODEL, {
    device: 'webgpu',
    // Moonshine: fp32 encoder, q4 decoder is the recommended browser combo.
    dtype: { encoder_model: 'fp32', decoder_model_merged: 'q4' },
    progress_callback,
  })) as AutomaticSpeechRecognitionPipeline;
  tts ??= await KokoroTTS.from_pretrained(TTS_MODEL, {
    dtype: 'fp32', // WebGPU recommends fp32 for Kokoro
    device: 'webgpu',
    progress_callback,
  });
  post({ type: 'ready' });
}

self.addEventListener('message', async (e: MessageEvent) => {
  const { type, data } = e.data ?? {};
  try {
    if (type === 'load') {
      await load();
    } else if (type === 'transcribe') {
      if (!asr) throw new Error('STT not loaded');
      const out = await asr(data.audio as Float32Array);
      const text = (Array.isArray(out) ? out[0]?.text : out?.text) ?? '';
      post({ type: 'transcript', text: String(text).trim() });
    } else if (type === 'synthesize') {
      if (!tts) throw new Error('TTS not loaded');
      const audio = await tts.generate(data.text as string, { voice: data.voice ?? 'af_heart' });
      const pcm = audio.audio as Float32Array;
      post({ type: 'audio', pcm, rate: audio.sampling_rate }, [pcm.buffer]);
    }
  } catch (err) {
    post({ type: 'error', data: String((err as Error)?.message ?? err) });
  }
});
