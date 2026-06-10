import * as THREE from 'three';
import type { Mood, ParamMap } from '../types';
import type { StateSnapshot } from '../stateSnapshot';

/**
 * Everything a binding can read on a given frame. Built once per frame
 * by DefinitionOrb and handed to the binding runtime.
 */
export interface SignalContext {
  time: number;
  botLevel: number;
  userLevel: number;
  breath: number;
  clickStrength: number;
  mood: Mood;
  snap: StateSnapshot;
  params: ParamMap;
}

export function resolveScalar(signal: string, ctx: SignalContext): number | undefined {
  switch (signal) {
    case 'time': return ctx.time;
    case 'bot.level': return ctx.botLevel;
    case 'user.level': return ctx.userLevel;
    case 'breath': return ctx.breath;
    case 'pointer.clickStrength': return ctx.clickStrength;
    case 'mood.valence': return ctx.mood.valence;
    case 'mood.arousal': return ctx.mood.arousal;
    case 'mood.guardedness': return ctx.mood.guardedness;
    case 'snap.density': return ctx.snap.density;
    case 'snap.glow': return ctx.snap.glow;
    case 'snap.speed': return ctx.snap.speed;
    case 'snap.ca': return ctx.snap.ca;
    case 'snap.asymmetry': return ctx.snap.asymmetry;
    case 'snap.rotation': return ctx.snap.rotation;
    case 'snap.scale': return ctx.snap.scale;
  }
  if (signal.startsWith('param.')) {
    const v = ctx.params[signal.slice('param.'.length)];
    return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
  }
  return undefined;
}

const scratchColor = new THREE.Color();

/** Resolve a color-valued signal. Returns a shared scratch — copy it out. */
export function resolveColor(signal: string, ctx: SignalContext): THREE.Color | undefined {
  switch (signal) {
    case 'snap.primary': return ctx.snap.primary;
    case 'snap.secondary': return ctx.snap.secondary;
  }
  if (signal.startsWith('param.')) {
    const v = ctx.params[signal.slice('param.'.length)];
    if (typeof v === 'string' && v.length > 0) {
      try {
        scratchColor.set(v);
        return scratchColor;
      } catch {
        return undefined;
      }
    }
  }
  return undefined;
}
