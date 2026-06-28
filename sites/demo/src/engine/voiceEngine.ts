/**
 * Voice orchestrator — the in-browser pipeline behind the orb.
 *
 * Wires mic capture → STT (Moonshine) → LLM (Gemma) → TTS (Kokoro) →
 * playback, emitting the exact orbis-sse events the real app consumes
 * (session / bot-state / transcript). The orb pulses through the levels
 * bus (audio.ts → get_audio_levels), so listening/speaking look alive.
 *
 * Voice-first: one entry point — activate() (the orb tap / mic button) —
 * loads what's needed on first use (priming mic permission within the
 * gesture first), then runs the loop. Typing is a thin fallback that goes
 * through the same handleInput → speaks the reply too.
 */
import { emitSse } from '../tauri-shim/bus';
import { gemmaEngine, type ProgressCb } from './gemmaEngine';
import {
  primeMicPermission,
  startCapture,
  stopCapture,
  beginPlayback,
  enqueuePlayback,
  endPlayback,
} from './audio';
import { trackProgress, type FileProgress } from './progress';

const VOICE = 'af_heart';

class VoiceEngine {
  onProgress: ProgressCb | null = null;

  private speech: Worker | null = null;
  private speechLoaded = false;
  private speechLoading: Promise<void> | null = null;
  private speechResolve: (() => void) | null = null;
  private speechReject: ((e: Error) => void) | null = null;
  private pendingTranscribe: ((t: string) => void) | null = null;
  private speechFileProg: FileProgress = new Map();

  private listening = false;
  private busy = false;
  private transitioning = false;
  private firstChunk = false;

  /** Open a session so the app's bridge flips to connected/idle. */
  init(): void {
    emitSse('session', { event: 'start', session_id: 'demo' });
    emitSse('bot-state', { state: 'idle' });
  }

  get ready(): boolean {
    return gemmaEngine.isLoaded && this.speechLoaded;
  }
  get voiceReady(): boolean {
    return this.speechLoaded;
  }
  get isListening(): boolean {
    return this.listening;
  }
  get isBusy(): boolean {
    return this.busy || this.transitioning;
  }

  // ---- loading ----
  async load(onProgress: ProgressCb): Promise<void> {
    this.onProgress = onProgress;
    await gemmaEngine.load((s, p) => onProgress(s === 'ready' ? 'Brain ready' : 'Downloading brain', p));
    await this.loadSpeech((s, p) => onProgress(s === 'ready' ? 'Voice ready' : 'Downloading voice', p));
  }

  private loadSpeech(onProgress: ProgressCb): Promise<void> {
    if (this.speechLoaded) return Promise.resolve();
    if (this.speechLoading) return this.speechLoading;
    if (!this.speech) {
      this.speech = new Worker(new URL('./speech.worker.ts', import.meta.url), {
        type: 'module',
      });
      this.speech.addEventListener('message', (e) => this.onSpeech(e, onProgress));
    }
    this.speechLoading = new Promise<void>((resolve, reject) => {
      this.speechResolve = resolve;
      this.speechReject = reject;
    });
    this.speech.postMessage({ type: 'load' });
    return this.speechLoading;
  }

  // ---- the one-tap entry point (orb tap / mic button) ----
  async activate(): Promise<void> {
    if (this.busy || this.transitioning) return;
    if (this.listening) {
      await this.stopListening();
      return;
    }
    if (!this.ready) {
      this.transitioning = true;
      try {
        await primeMicPermission(); // bank permission inside the user gesture
        await this.load(this.onProgress ?? (() => {}));
      } finally {
        this.transitioning = false;
      }
    }
    await this.startListening();
  }

  /** For the orb's set_mic_listening invoke. */
  setListening(on: boolean): void {
    if (on) {
      if (!this.listening && !this.busy && !this.transitioning) void this.activate();
    } else {
      void this.stopListening();
    }
  }

  private async startListening(): Promise<void> {
    if (!this.ready || this.listening || this.busy) return;
    this.listening = true;
    emitSse('bot-state', { state: 'listening' });
    try {
      await startCapture(() => {
        void this.stopListening();
      });
    } catch {
      this.listening = false;
      emitSse('bot-state', { state: 'idle' });
    }
  }

  private async stopListening(): Promise<void> {
    if (!this.listening) return;
    this.listening = false;
    const pcm = await stopCapture();
    console.info('[orbis-demo] captured', pcm.length, 'samples');
    if (pcm.length < 8000) {
      // < ~0.5s of audio — treat as a mis-tap.
      emitSse('bot-state', { state: 'idle' });
      return;
    }
    emitSse('bot-state', { state: 'thinking' });
    const text = await this.transcribe(pcm);
    console.info('[orbis-demo] transcript:', JSON.stringify(text));
    if (!text) {
      emitSse('bot-state', { state: 'idle' });
      return;
    }
    await this.handleInput(text);
  }

  /** Typed fallback — loads on demand, then runs the same loop (speaks too). */
  async submitText(text: string): Promise<void> {
    if (this.busy || this.listening || this.transitioning) return;
    if (!this.ready) {
      this.transitioning = true;
      try {
        await this.load(this.onProgress ?? (() => {}));
      } finally {
        this.transitioning = false;
      }
    }
    await this.handleInput(text);
  }

  private async handleInput(text: string): Promise<void> {
    this.busy = true;
    this.firstChunk = true;
    emitSse('transcript', { source: 'user', text, final: true });
    emitSse('bot-state', { state: 'thinking' });
    try {
      // Open playback + a streaming TTS session, then pipe Gemma's tokens
      // into the splitter so it speaks sentence-by-sentence while generating.
      const drained = beginPlayback();
      this.speech!.postMessage({ type: 'tts-start', data: { voice: VOICE } });
      const reply = await gemmaEngine.complete(text, (full, delta) => {
        emitSse('transcript', { source: 'bot', text: full, final: false });
        if (delta) this.speech!.postMessage({ type: 'tts-push', data: { text: delta } });
      });
      console.info('[orbis-demo] reply:', JSON.stringify(reply));
      emitSse('transcript', { source: 'bot', text: reply, final: true });
      this.speech!.postMessage({ type: 'tts-close' });
      await drained; // resolves once all spoken audio has played out
    } finally {
      emitSse('bot-state', { state: 'idle' });
      this.busy = false;
    }
  }

  private transcribe(pcm: Float32Array): Promise<string> {
    return new Promise((resolve) => {
      this.pendingTranscribe = resolve;
      this.speech!.postMessage({ type: 'transcribe', data: { audio: pcm } }, [pcm.buffer]);
    });
  }

  private onSpeech(e: MessageEvent, onProgress: ProgressCb): void {
    const { type, data, text, pcm, rate } = e.data ?? {};
    switch (type) {
      case 'progress':
        onProgress('downloading', trackProgress(this.speechFileProg, data));
        break;
      case 'ready':
        this.speechLoaded = true;
        this.speechResolve?.();
        this.speechResolve = this.speechReject = null;
        break;
      case 'transcript':
        this.pendingTranscribe?.(String(text ?? ''));
        this.pendingTranscribe = null;
        break;
      case 'tts-chunk':
        if (this.firstChunk) {
          this.firstChunk = false;
          emitSse('bot-state', { state: 'speaking' });
        }
        enqueuePlayback(pcm as Float32Array, rate as number);
        break;
      case 'tts-done':
        endPlayback();
        break;
      case 'error':
        console.error('[orbis-demo] speech worker error:', data);
        this.speechReject?.(new Error(String(data)));
        this.speechResolve = this.speechReject = null;
        this.pendingTranscribe?.('');
        this.pendingTranscribe = null;
        // The turn resolves via tts-done → endPlayback; don't force idle here.
        break;
    }
  }
}

export const voiceEngine = new VoiceEngine();
