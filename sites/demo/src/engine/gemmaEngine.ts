/**
 * On-device LLM (Gemma) — worker wrapper.
 *
 * Pure LLM: load the model and complete a turn, streaming tokens via a
 * callback. It emits NO orbis-sse events — the voice orchestrator
 * (voiceEngine) owns conversation state + presentation so the same LLM
 * serves both the voice loop and the typed fallback.
 */
export type ProgressCb = (status: string, pct: number | null) => void;

const SYSTEM =
  'You are ORBIS, a warm, concise voice companion running entirely on the ' +
  "user's device in their browser. Reply in a natural, spoken style — 1 to 2 " +
  'short sentences, no markdown, no lists, no emoji. If asked what you are, ' +
  'mention you are a preview of ORBIS running fully on-device (Gemma for the ' +
  'brain, with speech in and out) — nothing is sent to a server.';

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
  private onToken: ((full: string) => void) | null = null;
  private genResolve: (() => void) | null = null;

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

  /** Complete one turn. Streams the running text via onToken; resolves with
   *  the full reply. No state events — the caller presents it. */
  async complete(userText: string, onToken?: (full: string) => void): Promise<string> {
    if (!this.loaded || !this.worker) return '';
    this.messages.push({ role: 'user', content: userText });
    this.acc = '';
    this.onToken = onToken ?? null;
    await new Promise<void>((resolve) => {
      this.genResolve = resolve;
      this.worker!.postMessage({ type: 'generate', data: this.messages });
    });
    this.messages.push({ role: 'assistant', content: this.acc });
    return this.acc;
  }

  private onMessage(e: MessageEvent): void {
    const { type, data } = e.data ?? {};
    switch (type) {
      case 'progress': {
        const d = data as ProgressData;
        const pct = typeof d?.progress === 'number' ? d.progress : null;
        const label =
          d?.status === 'progress' ? `downloading ${d?.file ?? 'model'}` : (d?.status ?? 'loading');
        this.onProgress?.(label, pct);
        break;
      }
      case 'ready':
        this.loaded = true;
        this.onProgress?.('ready', 100);
        this.loadResolve?.();
        this.loadResolve = this.loadReject = null;
        break;
      case 'token':
        this.acc += String(data ?? '');
        this.onToken?.(this.acc);
        break;
      case 'done':
        this.genResolve?.();
        this.genResolve = null;
        break;
      case 'error':
        this.onProgress?.('error: ' + String(data), null);
        this.loadReject?.(new Error(String(data)));
        this.loadResolve = this.loadReject = null;
        this.genResolve?.();
        this.genResolve = null;
        break;
    }
  }
}

export const gemmaEngine = new GemmaEngine();
