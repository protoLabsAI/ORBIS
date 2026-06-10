/**
 * Hand-rolled `.orbis` validator — no schema-library dependency so the
 * package stays dependency-free and the Python sidecar can mirror the
 * exact same rules (agent/orb_definitions.py).
 *
 * Returns every problem found (not first-error) so the editor can show
 * a complete diagnostic list.
 */

import type { FieldSpec, SectionId } from '../types';
import {
  ORBIS_FORMAT,
  ORBIS_VERSION,
  type Binding,
  type OrbDefinition,
  type UniformDecl,
  type UniformType,
} from './types';
import { RESERVED_BINDING_TARGETS, STANDARD_UNIFORM_NAMES } from './prelude';

/** Hard caps — mirror these in the sidecar validator. */
export const MAX_FRAGMENT_CHARS = 256_000;
export const MAX_DEFINITION_CHARS = 512_000;
export const MAX_UNIFORMS = 64;
export const MAX_BINDINGS = 128;
export const MAX_FIELDS = 64;
export const MAX_PALETTES = 32;

const ID_RE = /^[a-z0-9][a-z0-9-]{1,63}$/;
const UNIFORM_NAME_RE = /^u[A-Za-z0-9_]{1,63}$/;
const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/;

const UNIFORM_TYPES: ReadonlySet<string> = new Set(['float', 'vec2', 'vec3', 'vec4', 'color']);
const SECTIONS: ReadonlySet<string> = new Set(['color', 'energy', 'motion', 'fractal', 'perf']);
const OPS: ReadonlySet<string> = new Set(['set', 'add', 'mul']);
const CURVES: ReadonlySet<string> = new Set(['linear', 'exp', 'smoothstep']);
const COMPONENTS: ReadonlySet<string> = new Set(['x', 'y', 'z', 'w']);

const SCALAR_SIGNALS: ReadonlySet<string> = new Set([
  'time', 'bot.level', 'user.level', 'breath', 'pointer.clickStrength',
  'mood.valence', 'mood.arousal', 'mood.guardedness',
  'snap.density', 'snap.glow', 'snap.speed', 'snap.ca', 'snap.asymmetry',
  'snap.rotation', 'snap.scale',
]);
const COLOR_SIGNALS: ReadonlySet<string> = new Set(['snap.primary', 'snap.secondary']);

export type ValidationResult =
  | { ok: true; def: OrbDefinition; warnings: string[] }
  | { ok: false; errors: string[]; warnings: string[] };

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isStr = (v: unknown): v is string => typeof v === 'string';

export function validateOrbDefinition(value: unknown): ValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];
  const err = (m: string) => errors.push(m);

  if (!isObj(value)) {
    return { ok: false, errors: ['definition must be a JSON object'], warnings };
  }
  const v = value;

  // Total-size cap up front — a hostile 50MB "fragment" shouldn't get
  // a detailed walk.
  try {
    if (JSON.stringify(v).length > MAX_DEFINITION_CHARS) {
      return { ok: false, errors: [`definition exceeds ${MAX_DEFINITION_CHARS} chars`], warnings };
    }
  } catch {
    return { ok: false, errors: ['definition is not serializable JSON'], warnings };
  }

  if (v.format !== ORBIS_FORMAT) err(`format must be "${ORBIS_FORMAT}"`);
  if (v.version !== ORBIS_VERSION) err(`version must be ${ORBIS_VERSION}`);
  if (!isStr(v.id) || !ID_RE.test(v.id)) err('id must be a slug ([a-z0-9-], 2-64 chars)');
  if (!isStr(v.name) || v.name.trim().length === 0 || v.name.length > 120) {
    err('name must be a non-empty string (≤120 chars)');
  }
  if (v.engine !== 'raymarch-v1') err('engine must be "raymarch-v1" (the only v1 engine)');

  // — shaders —
  const shaders = isObj(v.shaders) ? v.shaders : undefined;
  if (!shaders || !isStr(shaders.fragment) || shaders.fragment.trim().length === 0) {
    err('shaders.fragment must be a non-empty GLSL string');
  } else {
    if (shaders.fragment.length > MAX_FRAGMENT_CHARS) {
      err(`shaders.fragment exceeds ${MAX_FRAGMENT_CHARS} chars`);
    }
    if (!shaders.fragment.includes('gl_FragColor')) {
      warnings.push('shaders.fragment never writes gl_FragColor');
    }
  }

  // — uniforms —
  const uniformNames = new Set<string>(STANDARD_UNIFORM_NAMES);
  const uniformDecls = new Map<string, UniformDecl>();
  if (!isObj(v.uniforms)) {
    err('uniforms must be an object (may be empty)');
  } else {
    const entries = Object.entries(v.uniforms);
    if (entries.length > MAX_UNIFORMS) err(`more than ${MAX_UNIFORMS} uniforms`);
    for (const [name, decl] of entries) {
      if (!UNIFORM_NAME_RE.test(name)) {
        err(`uniform "${name}" — names must match ${UNIFORM_NAME_RE}`);
        continue;
      }
      if ((STANDARD_UNIFORM_NAMES as readonly string[]).includes(name)) {
        err(`uniform "${name}" shadows a standard uniform`);
        continue;
      }
      if (!isObj(decl) || !isStr(decl.type) || !UNIFORM_TYPES.has(decl.type)) {
        err(`uniform "${name}" — type must be one of float|vec2|vec3|vec4|color`);
        continue;
      }
      const type = decl.type as UniformType;
      const d = decl.default;
      if (d !== undefined && !validDefault(type, d)) {
        err(`uniform "${name}" — default doesn't match type ${type}`);
        continue;
      }
      uniformNames.add(name);
      uniformDecls.set(name, { type, default: d as UniformDecl['default'] });
    }
  }

  // — fields —
  const fieldKeys = new Set<string>();
  if (!Array.isArray(v.fields)) {
    err('fields must be an array (may be empty)');
  } else {
    if (v.fields.length > MAX_FIELDS) err(`more than ${MAX_FIELDS} fields`);
    v.fields.forEach((f, i) => {
      const e = validateField(f, i);
      if (e) err(e);
      else fieldKeys.add((f as FieldSpec).key);
    });
  }

  // — palettes —
  if (!isObj(v.palettes) || Object.keys(v.palettes).length === 0) {
    err('palettes must be a non-empty object');
  } else {
    const names = Object.keys(v.palettes);
    if (names.length > MAX_PALETTES) err(`more than ${MAX_PALETTES} palettes`);
    for (const [pname, palette] of Object.entries(v.palettes)) {
      if (!isObj(palette)) {
        err(`palette "${pname}" must be an object`);
        continue;
      }
      for (const [k, pv] of Object.entries(palette)) {
        if (!isNum(pv) && !isStr(pv)) err(`palette "${pname}".${k} must be number or string`);
      }
    }
    if (!isStr(v.defaultPalette) || !names.includes(v.defaultPalette)) {
      err('defaultPalette must name an existing palette');
    }
  }

  // — bindings —
  if (!Array.isArray(v.bindings)) {
    err('bindings must be an array (may be empty)');
  } else {
    if (v.bindings.length > MAX_BINDINGS) err(`more than ${MAX_BINDINGS} bindings`);
    v.bindings.forEach((b, i) => {
      for (const e of validateBinding(b, i, uniformNames, uniformDecls, fieldKeys)) err(e);
    });
  }

  // — moodDefaults (optional) —
  if (v.moodDefaults != null) {
    if (!isObj(v.moodDefaults)) err('moodDefaults must be an object or null');
    else {
      for (const [dim, deltas] of Object.entries(v.moodDefaults)) {
        if (!['valence', 'arousal', 'guardedness'].includes(dim)) {
          err(`moodDefaults.${dim} — unknown mood dimension`);
        } else if (!isObj(deltas)) {
          err(`moodDefaults.${dim} must be an object of param deltas`);
        }
      }
    }
  }

  // — optional simple blocks: shape-check loosely —
  if (v.geometry != null && !isObj(v.geometry)) err('geometry must be an object');
  if (v.material != null && !isObj(v.material)) err('material must be an object');
  if (v.motion != null && !isObj(v.motion)) err('motion must be an object');
  if (v.post != null && !isObj(v.post)) err('post must be an object or null');

  if (errors.length > 0) return { ok: false, errors, warnings };
  return { ok: true, def: v as unknown as OrbDefinition, warnings };
}

function validDefault(type: UniformType, d: unknown): boolean {
  switch (type) {
    case 'float':
      return isNum(d);
    case 'color':
      return isStr(d) && HEX_COLOR_RE.test(d);
    case 'vec2':
    case 'vec3':
    case 'vec4': {
      const n = { vec2: 2, vec3: 3, vec4: 4 }[type];
      return Array.isArray(d) && d.length === n && d.every(isNum);
    }
  }
}

function validateField(f: unknown, i: number): string | null {
  if (!isObj(f)) return `fields[${i}] must be an object`;
  if (!isStr(f.key) || f.key.length === 0 || f.key.length > 64) return `fields[${i}].key invalid`;
  if (!isStr(f.label) || f.label.length === 0 || f.label.length > 64) return `fields[${i}].label invalid`;
  if (!isStr(f.section) || !SECTIONS.has(f.section as SectionId)) {
    return `fields[${i}].section must be color|energy|motion|fractal|perf`;
  }
  if (f.kind === 'color') return null;
  if (f.kind === 'slider') {
    if (!isNum(f.min) || !isNum(f.max) || !isNum(f.step) || f.step <= 0 || f.max <= f.min) {
      return `fields[${i}] slider needs numeric min < max and step > 0`;
    }
    return null;
  }
  return `fields[${i}].kind must be color|slider`;
}

function validateBinding(
  b: unknown,
  i: number,
  uniformNames: ReadonlySet<string>,
  uniformDecls: ReadonlyMap<string, UniformDecl>,
  fieldKeys: ReadonlySet<string>,
): string[] {
  const errs: string[] = [];
  if (!isObj(b)) return [`bindings[${i}] must be an object`];
  const bd = b as Partial<Binding>;

  // Target: uniform name with optional component suffix.
  if (!isStr(bd.target)) {
    errs.push(`bindings[${i}].target must be a string`);
    return errs;
  }
  const [uName, comp, extra] = bd.target.split('.');
  if (extra !== undefined || (comp !== undefined && !COMPONENTS.has(comp))) {
    errs.push(`bindings[${i}].target "${bd.target}" — component must be one of x|y|z|w`);
    return errs;
  }
  if (RESERVED_BINDING_TARGETS.has(uName)) {
    errs.push(`bindings[${i}].target "${uName}" is engine-managed and cannot be bound`);
  }
  if (!uniformNames.has(uName)) {
    errs.push(`bindings[${i}].target "${uName}" is not a declared or standard uniform`);
  }
  const decl = uniformDecls.get(uName);
  const targetIsColor =
    decl?.type === 'color' || uName === 'uPrimaryColor' || uName === 'uSecondaryColor';
  if (comp !== undefined) {
    if (decl === undefined || decl.type === 'float' || decl.type === 'color') {
      errs.push(`bindings[${i}].target "${bd.target}" — component suffix needs a vec uniform`);
    } else {
      const arity = { vec2: 2, vec3: 3, vec4: 4 }[decl.type];
      const idx = ['x', 'y', 'z', 'w'].indexOf(comp);
      if (idx >= arity) errs.push(`bindings[${i}].target "${bd.target}" — out of range for ${decl.type}`);
    }
  }

  // Signal.
  if (!isStr(bd.signal)) {
    errs.push(`bindings[${i}].signal must be a string`);
    return errs;
  }
  const isParam = bd.signal.startsWith('param.');
  const paramKey = isParam ? bd.signal.slice('param.'.length) : null;
  const isScalarSig = SCALAR_SIGNALS.has(bd.signal) || isParam;
  const isColorSig = COLOR_SIGNALS.has(bd.signal) || isParam;
  if (!isScalarSig && !isColorSig) {
    errs.push(`bindings[${i}].signal "${bd.signal}" is unknown`);
  }
  if (isParam && paramKey && !fieldKeys.has(paramKey)) {
    // Param signals usually point at field keys; palettes can carry
    // extra non-field params, so this is a warning-grade situation —
    // but without field metadata we can't know, so allow silently.
  }

  // Color targets: only color-capable signals, only 'set', no component.
  if (targetIsColor && comp === undefined) {
    if (!isColorSig) errs.push(`bindings[${i}] — color target "${uName}" needs a color signal`);
    if (bd.op !== undefined && bd.op !== 'set') {
      errs.push(`bindings[${i}] — color targets only support op "set"`);
    }
  }

  if (bd.op !== undefined && !OPS.has(bd.op)) errs.push(`bindings[${i}].op must be set|add|mul`);
  if (bd.curve !== undefined && !CURVES.has(bd.curve)) {
    errs.push(`bindings[${i}].curve must be linear|exp|smoothstep`);
  }
  if (bd.scale !== undefined && !isNum(bd.scale)) errs.push(`bindings[${i}].scale must be a number`);
  if (bd.offset !== undefined && !isNum(bd.offset)) errs.push(`bindings[${i}].offset must be a number`);
  if (bd.smooth !== undefined && (!isNum(bd.smooth) || bd.smooth <= 0 || bd.smooth > 1)) {
    errs.push(`bindings[${i}].smooth must be in (0, 1]`);
  }
  return errs;
}
