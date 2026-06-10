import * as THREE from 'three';
import type { Binding, OrbDefinition } from '../definition/types';
import { lerp } from '../math';
import { scalarDefault, type UniformRecord } from './uniforms';
import { resolveColor, resolveScalar, type SignalContext } from './signals';

/**
 * Per-frame binding evaluator. Bindings are grouped by target; each
 * target's accumulator starts from the uniform's declared default every
 * frame, then bindings apply in declaration order:
 *
 *   value = curve(signal) * scale + offset
 *   acc   = acc <op> value          (set | add | mul)
 *
 * Optional per-binding one-pole smoothing is applied to the *resolved
 * value* (before the op), with state held across frames per binding.
 */

interface CompiledScalarBinding {
  kind: 'scalar';
  uniform: string;
  component: number; // -1 = whole float uniform
  signal: string;
  op: 'set' | 'add' | 'mul';
  scale: number;
  offset: number;
  curve: 'linear' | 'exp' | 'smoothstep';
  smooth: number; // 0 = off
  smoothState: number;
  smoothInit: boolean;
}

interface CompiledColorBinding {
  kind: 'color';
  uniform: string;
  signal: string;
}

type Compiled = CompiledScalarBinding | CompiledColorBinding;

const COMPONENT_KEYS = ['x', 'y', 'z', 'w'] as const;

function applyCurve(v: number, curve: CompiledScalarBinding['curve']): number {
  switch (curve) {
    case 'linear': return v;
    case 'exp': return v * v;
    case 'smoothstep': {
      const t = v < 0 ? 0 : v > 1 ? 1 : v;
      return t * t * (3 - 2 * t);
    }
  }
}

export class BindingRuntime {
  private compiled: Compiled[] = [];
  /** Scalar targets that have ≥1 binding, with their per-frame reset default. */
  private scalarTargets = new Map<string, number>();
  private def: OrbDefinition;

  constructor(def: OrbDefinition) {
    this.def = def;
    for (const b of def.bindings) this.compile(b);
  }

  private compile(b: Binding): void {
    const [uName, comp] = b.target.split('.');
    const decl = this.def.uniforms[uName];
    const isColorTarget =
      comp === undefined &&
      (decl?.type === 'color' || uName === 'uPrimaryColor' || uName === 'uSecondaryColor');

    if (isColorTarget) {
      this.compiled.push({ kind: 'color', uniform: uName, signal: b.signal });
      return;
    }

    const component = comp === undefined ? -1 : COMPONENT_KEYS.indexOf(comp as 'x');
    const key = b.target;
    if (!this.scalarTargets.has(key)) {
      this.scalarTargets.set(key, component === -1 ? scalarDefault(this.def, uName) : NaN);
    }
    this.compiled.push({
      kind: 'scalar',
      uniform: uName,
      component,
      signal: b.signal,
      op: b.op ?? 'set',
      scale: b.scale ?? 1,
      offset: b.offset ?? 0,
      curve: b.curve ?? 'linear',
      smooth: b.smooth ?? 0,
      smoothState: 0,
      smoothInit: false,
    });
  }

  /** Evaluate all bindings into `uniforms` for this frame. */
  apply(ctx: SignalContext, uniforms: UniformRecord): void {
    // Scalar accumulators reset to declared defaults each frame.
    const acc = new Map<string, number>();
    for (const [key, dflt] of this.scalarTargets) {
      if (Number.isNaN(dflt)) {
        // Component target — start from the uniform's current component
        // value (its constructed default; components without bindings
        // stay untouched).
        const [uName, comp] = key.split('.');
        const u = uniforms[uName];
        const idx = COMPONENT_KEYS.indexOf(comp as 'x');
        const vec = u?.value as { getComponent?: (i: number) => number } | undefined;
        acc.set(key, vec?.getComponent ? vec.getComponent(idx) : 0);
      } else {
        acc.set(key, dflt);
      }
    }

    for (const c of this.compiled) {
      if (c.kind === 'color') {
        const color = resolveColor(c.signal, ctx);
        const u = uniforms[c.uniform];
        if (color && u && u.value instanceof THREE.Color) u.value.copy(color);
        continue;
      }

      const raw = resolveScalar(c.signal, ctx);
      if (raw === undefined) continue;
      let value = applyCurve(raw, c.curve) * c.scale + c.offset;
      if (c.smooth > 0) {
        if (!c.smoothInit) {
          c.smoothState = value;
          c.smoothInit = true;
        } else {
          c.smoothState = lerp(c.smoothState, value, c.smooth);
        }
        value = c.smoothState;
      }

      const key = c.component === -1 ? c.uniform : `${c.uniform}.${COMPONENT_KEYS[c.component]}`;
      const prev = acc.get(key) ?? 0;
      const next = c.op === 'set' ? value : c.op === 'add' ? prev + value : prev * value;
      acc.set(key, next);
    }

    // Write accumulators into the uniform values.
    for (const [key, value] of acc) {
      const [uName, comp] = key.split('.');
      const u = uniforms[uName];
      if (!u) continue;
      if (comp === undefined) {
        if (typeof u.value === 'number') u.value = value;
      } else {
        const idx = COMPONENT_KEYS.indexOf(comp as 'x');
        const vec = u.value as { setComponent?: (i: number, v: number) => void };
        vec.setComponent?.(idx, value);
      }
    }
  }
}
