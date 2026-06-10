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
