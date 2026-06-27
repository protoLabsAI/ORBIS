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
import { primeMicPermission, startCapture, stopCapture, playPCM } from './audio';

const VOICE = 'af_heart';

class VoiceEngine {
  onProgress: ProgressCb | null = null;

  private speech: Worker | null = null;
  private speechLoaded = false;
  private speechLoading: Promise<void> | null = null;
  private speechResolve: (() => void) | null = null;
  private speechReject: ((e: Error) => void) | null = null;
  private pendingTranscribe: ((t: string) => void) | null = null;
  private pendingAudio: ((a: { pcm: Float32Array; rate: number }) => void) | null = null;

  private listening = false;
  private busy = false;
  private transitioning = false;

  /** Open a session so the app's bridge flips to connected/idle. */
  init(): void {
    emitSse('session', { event: 'start', session_id: 'demo' });
    emitSse('bot-state', { state: 'idle' });
  }

  get ready(): boolean {
    return gemmaEngine.isLoaded && this.speechLoaded;
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
    await gemmaEngine.load((s, p) => onProgress(`brain · ${s}`, p));
    await this.loadSpeech((s, p) => onProgress(`voice · ${s}`, p));
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
      await startCapture();
    } catch {
      this.listening = false;
      emitSse('bot-state', { state: 'idle' });
    }
  }

  private async stopListening(): Promise<void> {
    if (!this.listening) return;
    this.listening = false;
    const pcm = await stopCapture();
    if (pcm.length < 8000) {
      // < ~0.5s of audio — treat as a mis-tap.
      emitSse('bot-state', { state: 'idle' });
      return;
    }
    emitSse('bot-state', { state: 'thinking' });
    const text = await this.transcribe(pcm);
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
    emitSse('transcript', { source: 'user', text, final: true });
    emitSse('bot-state', { state: 'thinking' });
    const reply = await gemmaEngine.complete(text, (full) =>
      emitSse('transcript', { source: 'bot', text: full, final: false }),
    );
    emitSse('transcript', { source: 'bot', text: reply, final: true });
    if (reply.trim()) {
      emitSse('bot-state', { state: 'speaking' });
      try {
        const { pcm, rate } = await this.synthesize(reply);
        await playPCM(pcm, rate);
      } catch {
        /* TTS failed — still finish the turn */
      }
    }
    emitSse('bot-state', { state: 'idle' });
    this.busy = false;
  }

  private transcribe(pcm: Float32Array): Promise<string> {
    return new Promise((resolve) => {
      this.pendingTranscribe = resolve;
      this.speech!.postMessage({ type: 'transcribe', data: { audio: pcm } }, [pcm.buffer]);
    });
  }

  private synthesize(text: string): Promise<{ pcm: Float32Array; rate: number }> {
    return new Promise((resolve) => {
      this.pendingAudio = resolve;
      this.speech!.postMessage({ type: 'synthesize', data: { text, voice: VOICE } });
    });
  }

  private onSpeech(e: MessageEvent, onProgress: ProgressCb): void {
    const { type, data, text, pcm, rate } = e.data ?? {};
    switch (type) {
      case 'progress': {
        const pct = typeof data?.progress === 'number' ? data.progress : null;
        onProgress(
          data?.status === 'progress' ? `downloading ${data?.file ?? 'model'}` : (data?.status ?? 'loading'),
          pct,
        );
        break;
      }
      case 'ready':
        this.speechLoaded = true;
        this.speechResolve?.();
        this.speechResolve = this.speechReject = null;
        break;
      case 'transcript':
        this.pendingTranscribe?.(String(text ?? ''));
        this.pendingTranscribe = null;
        break;
      case 'audio':
        this.pendingAudio?.({ pcm: pcm as Float32Array, rate: rate as number });
        this.pendingAudio = null;
        break;
      case 'error':
        this.speechReject?.(new Error(String(data)));
        this.speechResolve = this.speechReject = null;
        this.pendingTranscribe?.('');
        this.pendingTranscribe = null;
        emitSse('bot-state', { state: 'idle' });
        this.busy = false;
        break;
    }
  }
}

export const voiceEngine = new VoiceEngine();
