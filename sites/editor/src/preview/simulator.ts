/**
 * Signal simulator — produces the enveloped [0,1] bot/user levels the
 * app's useAudioEnvelopes would, from synthetic or real sources:
 *
 *   pulse  — speech-ish bursts (syllable-rate sine gated by a slow gate)
 *   manual — a slider value, passed through
 *   mic    — real microphone RMS via Web Audio (this is a website, not
 *            the app runtime — getUserMedia is fine here)
 *
 * Levels are written into refs at rAF rate; DefinitionOrb reads them in
 * its own frame loop exactly like the app does.
 */

import { Envelope, ENV_BOT, ENV_USER, DISP_ALPHA, clamp01, lerp } from '@orbis/orb-runtime';
import type { LevelMode } from '@orbis/editor-ui';

export class LevelSimulator {
  readonly botRef: { current: number } = { current: 0 };
  readonly userRef: { current: number } = { current: 0 };

  private botMode: LevelMode = 'off';
  private userMode: LevelMode = 'off';
  private botManual = 0.5;
  private userManual = 0.5;

  private envBot = new Envelope(ENV_BOT);
  private envUser = new Envelope(ENV_USER);

  private raf = 0;
  private startMs = performance.now();

  private audioCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private micStream: MediaStream | null = null;
  private micBuf: Uint8Array | null = null;
  private micError = '';

  start(): void {
    const tick = () => {
      const t = (performance.now() - this.startMs) / 1000;
      // Final display-stage smoothing (DISP_ALPHA), same as the app's
      // useAudioEnvelopes — without it the synthetic pulse strobes far
      // harder than ORBIS ever renders. Photosensitivity matters here.
      const bot = this.level(this.botMode, this.botManual, t, this.envBot, 0);
      const user = this.level(this.userMode, this.userManual, t, this.envUser, 2.13);
      this.botRef.current = lerp(this.botRef.current, bot, DISP_ALPHA);
      this.userRef.current = lerp(this.userRef.current, user, DISP_ALPHA);
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  stop(): void {
    cancelAnimationFrame(this.raf);
    this.stopMic();
  }

  configure(opts: {
    botMode: LevelMode;
    userMode: LevelMode;
    botManual: number;
    userManual: number;
  }): void {
    this.botMode = opts.botMode;
    this.userMode = opts.userMode;
    this.botManual = opts.botManual;
    this.userManual = opts.userManual;
    if (opts.userMode === 'mic') this.startMic();
    else this.stopMic();
  }

  get micFailure(): string {
    return this.micError;
  }

  private level(
    mode: LevelMode,
    manual: number,
    t: number,
    env: Envelope,
    phase: number,
  ): number {
    switch (mode) {
      case 'off':
        return env.update(0);
      case 'manual':
        // Manual is a steady target level — bypass the envelope (the
        // display smoother in start() still eases slider jumps).
        return clamp01(manual);
      case 'pulse': {
        // Gentle speech-like modulation: slow syllable swell (~1.8 Hz,
        // shallow) inside soft phrase bursts. Deliberately below the
        // 3 Hz photosensitive-flash band and smoothstep-gated — no
        // hard on/off edges.
        const syllable = 0.65 + 0.35 * Math.sin(t * Math.PI * 2 * 1.8 + phase);
        const g = clamp01((Math.sin(t * Math.PI * 2 * 0.13 + phase) + 0.35) / 0.6);
        const gate = g * g * (3 - 2 * g);
        return env.update(syllable * gate * 0.22);
      }
      case 'mic': {
        if (!this.analyser || !this.micBuf) return env.update(0);
        this.analyser.getByteTimeDomainData(this.micBuf as Uint8Array<ArrayBuffer>);
        let sum = 0;
        for (let i = 0; i < this.micBuf.length; i++) {
          const v = (this.micBuf[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / this.micBuf.length);
        return env.update(Math.min(1, rms * 4));
      }
    }
  }

  private startMic(): void {
    if (this.micStream || this.micError) return;
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((stream) => {
        this.micStream = stream;
        this.audioCtx ??= new AudioContext();
        const source = this.audioCtx.createMediaStreamSource(stream);
        this.analyser = this.audioCtx.createAnalyser();
        this.analyser.fftSize = 1024;
        this.analyser.smoothingTimeConstant = 0.55;
        source.connect(this.analyser);
        this.micBuf = new Uint8Array(this.analyser.fftSize);
        if (this.audioCtx.state === 'suspended') this.audioCtx.resume().catch(() => {});
      })
      .catch((e: unknown) => {
        this.micError = e instanceof Error ? e.message : 'microphone unavailable';
      });
  }

  private stopMic(): void {
    this.micStream?.getTracks().forEach((t) => t.stop());
    this.micStream = null;
    this.analyser = null;
    this.micBuf = null;
    this.micError = '';
  }
}
