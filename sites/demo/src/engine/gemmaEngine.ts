/**
 * On-device voice engine — PR2 (text turns via Gemma).
 *
 * Owns the Gemma worker and translates a conversation into the orbis-sse
 * events the real app consumes: user text → `transcript`(user) + `thinking`;
 * first token → `speaking`; streamed tokens → `transcript`(bot, partial);
 * completion → `transcript`(bot, final) + `idle`. The orb, status pill, and
 * transcript surfaces react exactly as in the native app.
 *
 * PR3 adds Whisper/Moonshine STT + Kokoro TTS in front of send()/the
 * speaking phase — same event surface.
 */
import { emitSse } from '../tauri-shim/bus';

export type ProgressCb = (status: string, pct: number | null) => void;

const SYSTEM =
  'You are ORBIS, a warm, concise voice companion running entirely on the ' +
  "user's device in their browser. Reply in a natural, spoken style — 1 to 3 " +
  'short sentences, no markdown, no lists. If asked what you are, mention you ' +
  'are a preview of ORBIS running on-device (Gemma) with nothing sent to a server.';

interface ProgressData {
  status?: string;
  file?: string;
  progress?: number;
}

class GemmaEngine {
  private worker: Worker | null = null;
  private messages: Array<{ role: string; content: string }> = [
    { role: 'system', content: SYSTEM },
  ];
  private loaded = false;
  private loading: Promise<void> | null = null;
  private loadResolve: (() => void) | null = null;
  private loadReject: ((e: Error) => void) | null = null;
  private onProgress: ProgressCb | null = null;
  private acc = '';
  private genResolve: (() => void) | null = null;

  /** Announce a live session so the app's bridge flips to connected/idle. */
  init(): void {
    emitSse('session', { event: 'start', session_id: 'demo' });
    emitSse('bot-state', { state: 'idle' });
  }

  get isLoaded(): boolean {
    return this.loaded;
  }

  private ensureWorker(): void {
    if (this.worker) return;
    this.worker = new Worker(new URL('./llm.worker.ts', import.meta.url), {
      type: 'module',
    });
    this.worker.addEventListener('message', (e) => this.onMessage(e));
  }

  /** Download + compile the model (lazy, on first use). Idempotent. */
  load(onProgress: ProgressCb): Promise<void> {
    this.onProgress = onProgress;
    if (this.loaded) return Promise.resolve();
    if (this.loading) return this.loading;
    this.ensureWorker();
    this.loading = new Promise<void>((resolve, reject) => {
      this.loadResolve = resolve;
      this.loadReject = reject;
    });
    this.worker!.postMessage({ type: 'load' });
    return this.loading;
  }

  /** Run one user turn; resolves when the bot finishes speaking. */
  async send(text: string): Promise<void> {
    if (!this.loaded || !this.worker) return;
    this.messages.push({ role: 'user', content: text });
    emitSse('transcript', { source: 'user', text, final: true });
    emitSse('bot-state', { state: 'thinking' });
    this.acc = '';
    await new Promise<void>((resolve) => {
      this.genResolve = resolve;
      this.worker!.postMessage({ type: 'generate', data: this.messages });
    });
  }

  /** Stop the in-flight generation (PR3: verbal barge-in). */
  interrupt(): void {
    this.worker?.postMessage({ type: 'interrupt' });
  }

  private onMessage(e: MessageEvent): void {
    const { type, data } = e.data ?? {};
    switch (type) {
      case 'progress': {
        const d = data as ProgressData;
        const pct = typeof d?.progress === 'number' ? d.progress : null;
        const label =
          d?.status === 'progress'
            ? `Downloading ${d?.file ?? 'model'}…`
            : d?.status === 'done'
              ? 'Compiling…'
              : 'Loading…';
        this.onProgress?.(label, pct);
        break;
      }
      case 'ready': {
        this.loaded = true;
        this.onProgress?.('Ready', 100);
        this.loadResolve?.();
        this.loadResolve = this.loadReject = null;
        break;
      }
      case 'start':
        emitSse('bot-state', { state: 'speaking' });
        break;
      case 'token':
        this.acc += String(data ?? '');
        emitSse('transcript', { source: 'bot', text: this.acc, final: false });
        break;
      case 'done':
        emitSse('transcript', { source: 'bot', text: this.acc, final: true });
        emitSse('bot-state', { state: 'idle' });
        this.messages.push({ role: 'assistant', content: this.acc });
        this.genResolve?.();
        this.genResolve = null;
        break;
      case 'error':
        this.onProgress?.('Error: ' + String(data), null);
        emitSse('bot-state', { state: 'idle' });
        this.loadReject?.(new Error(String(data)));
        this.loadResolve = this.loadReject = null;
        this.genResolve?.();
        this.genResolve = null;
        break;
    }
  }
}

export const gemmaEngine = new GemmaEngine();
