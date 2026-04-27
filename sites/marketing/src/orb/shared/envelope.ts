import { clamp01 } from './math';
import { ENV_STAGE2, NORM_CEIL, NORM_FLOOR } from './constants';

/**
 * Asymmetric two-stage envelope follower. Maps raw RMS → [0, 1] with
 * heavy smoothing and fast-up / slow-down response.
 */
export class Envelope {
  private attack: number;
  private release: number;
  private s1 = 0;
  private s2 = 0;

  constructor({ attack, release }: { attack: number; release: number }) {
    this.attack = attack;
    this.release = release;
  }

  update(raw: number): number {
    const k1 = raw > this.s1 ? this.attack : this.release;
    this.s1 += (raw - this.s1) * k1;
    this.s2 += (this.s1 - this.s2) * ENV_STAGE2;
    return clamp01((this.s2 - NORM_FLOOR) / (NORM_CEIL - NORM_FLOOR));
  }

  reset() {
    this.s1 = 0;
    this.s2 = 0;
  }
}

/**
 * Byte-domain RMS from an AnalyserNode time-domain sample.
 *
 * `getByteTimeDomainData` is typed as expecting `Uint8Array<ArrayBuffer>`
 * in TS 6 / lib.dom.d.ts, but the buffer we create via `new Uint8Array(n)`
 * is inferred as `Uint8Array<ArrayBufferLike>`. Cast at the DOM-call site
 * rather than threading the generic through every caller.
 */
export function rmsFromAnalyser(analyser: AnalyserNode, buf: Uint8Array): number {
  analyser.getByteTimeDomainData(buf as Uint8Array<ArrayBuffer>);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) {
    const v = (buf[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / buf.length);
}
