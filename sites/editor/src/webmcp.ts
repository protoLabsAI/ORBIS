/**
 * WebMCP tool surface for the orb editor.
 *
 * Exposes the editor's full authoring power on the W3C WebMCP runtime
 * (`document.modelContext` / `navigator.modelContext`, polyfilled by
 * `@mcp-b/global`). An AI agent — Chrome's built-in agent, the MCP-B
 * inspector, or ORBIS driving an embedded webview — can author an entire
 * `.orbis`: write the fragment/vertex shader, declare uniforms, add
 * user-facing controls, wire signal→uniform bindings, edit metadata and
 * palettes, and import/export the whole definition.
 *
 * Design goals:
 *  - Context-friendly. `get_authoring_guide` hands the agent the GLSL contract,
 *    available uniforms/varyings, binding signals and limits up front so its
 *    shaders compile the first time. A single `set_control` (rather than one
 *    tool per field) keeps the tool list small.
 *  - Closed authoring loop. Every shader/structural write goes through the
 *    real validator (`validateOrbDefinition`) and, where it matters, the real
 *    WebGL compiler (`compileCheck`). Failures return the compiler/validator
 *    log verbatim and DO NOT commit — so the last good orb keeps rendering and
 *    the agent iterates against the error, exactly like a human in the editor.
 *  - Reuses the editor store and runtime (`setParam`/`updateDefinition`/…,
 *    `buildFragmentSource`/`compileCheck`) — no parallel authoring path.
 *
 * The page is a backendless SPA with no secrets, so every tool is a pure
 * client-state mutation and we accept the library default transport
 * (`allowedOrigins: ['*']`). The runtime is lazy-loaded, so the orb paints
 * before the MCP SDK arrives.
 */
import { initializeWebModelContext } from '@mcp-b/global';
// Importing a type from this package also activates its ambient `declare
// global` that types `document.modelContext` / `navigator.modelContext`.
import type { ToolDescriptor } from '@mcp-b/webmcp-types';
import {
  buildFragmentSource,
  compileCheck,
  validateOrbDefinition,
  MAX_FRAGMENT_CHARS,
  MAX_DEFINITION_CHARS,
  RESERVED_BINDING_TARGETS,
  type Binding,
  type OrbDefinition,
  type SectionId,
  type VoiceState,
} from '@orbis/orb-runtime';
import { store } from './state';

const VOICE_STATES: readonly VoiceState[] = ['idle', 'listening', 'thinking', 'speaking'];
const SECTIONS: readonly SectionId[] = ['color', 'energy', 'motion', 'fractal', 'perf'];
const UNIFORM_TYPES = ['float', 'vec2', 'vec3', 'vec4', 'color'] as const;
const BINDING_OPS = ['set', 'add', 'mul'] as const;
const BINDING_CURVES = ['linear', 'exp', 'smoothstep'] as const;

// Mirror of the (non-exported) signal sets in orb-runtime's validate.ts —
// surfaced to agents via get_authoring_guide. Keep in sync with that file.
const SCALAR_SIGNALS = [
  'time', 'bot.level', 'user.level', 'breath', 'pointer.clickStrength',
  'mood.valence', 'mood.arousal', 'mood.guardedness',
  'snap.density', 'snap.glow', 'snap.speed', 'snap.ca', 'snap.asymmetry',
  'snap.rotation', 'snap.scale', 'param.<key>',
];
const COLOR_SIGNALS = ['snap.primary', 'snap.secondary', 'param.<key> (string param)'];

/**
 * The runtime's `registerTool` is heavily overloaded for literal-schema
 * inference; we author descriptors dynamically, so we talk to it through the
 * plain descriptor shape.
 */
interface ModelContextLike {
  registerTool(tool: ToolDescriptor, options?: { signal?: AbortSignal }): void;
}

type ToolResult = { content: { type: 'text'; text: string }[]; isError?: boolean };

const ok = (text: string): ToolResult => ({ content: [{ type: 'text', text }] });
const err = (text: string): ToolResult => ({ content: [{ type: 'text', text }], isError: true });
const clamp = (n: number, min: number, max: number): number => Math.min(max, Math.max(min, n));
const def = (): OrbDefinition => store().getSnapshot().definition;

/**
 * Validate a candidate definition and (optionally) compile it before
 * committing. On any failure nothing is committed and the verbatim
 * validator/compiler log is returned so the agent can fix and retry.
 */
function commit(next: OrbDefinition, successMsg: string, opts?: { compile?: boolean }): ToolResult {
  const res = validateOrbDefinition(next);
  if (!res.ok) return err(`Invalid .orbis: ${res.errors.slice(0, 8).join('; ')}`);
  if (opts?.compile) {
    const c = compileCheck(res.def);
    if (!c.ok) {
      store().setShaderLog(c.log);
      return err(`Shader did not compile — not applied:\n${c.log}`);
    }
    store().setShaderLog('');
  }
  store().updateDefinition(() => res.def);
  const warn = res.warnings.length ? `\nwarnings: ${res.warnings.join('; ')}` : '';
  return ok(successMsg + warn);
}

/** Register every editor tool. Idempotent across calls. */
export function registerEditorTools(): void {
  // Idempotent — the auto-init on import has usually already run. Calling it
  // here also pins the import so the runtime is never tree-shaken away.
  initializeWebModelContext();

  const mc = document.modelContext as unknown as ModelContextLike | undefined;
  if (!mc) {
    console.warn('[webmcp] document.modelContext unavailable — agent tools disabled');
    return;
  }

  registerReadTools(mc);
  registerLiveTools(mc);
  registerShaderTools(mc);
  registerStructureTools(mc);
  registerIoTools(mc);
}

// ───────────────────────────── read / context ─────────────────────────────

function registerReadTools(mc: ModelContextLike): void {
  mc.registerTool({
    name: 'get_orb_state',
    description:
      'Read the orb at a glance: name, active palette and all palettes, every user-facing control (key, label, kind, numeric range) with its current value, plus the previewed voice state and mood. Use set_control to change a value.',
    annotations: { readOnlyHint: true, idempotentHint: true },
    inputSchema: { type: 'object', properties: {} },
    execute: () => {
      const s = store().getSnapshot();
      const controls = s.definition.fields.map((f) => ({
        key: f.key,
        label: f.label,
        section: f.section,
        kind: f.kind,
        ...(f.kind === 'slider' ? { min: f.min, max: f.max, step: f.step } : {}),
        value: s.params[f.key],
      }));
      return ok(
        JSON.stringify(
          {
            name: s.definition.name,
            id: s.definition.id,
            palette: s.palette,
            palettes: Object.keys(s.definition.palettes),
            controls,
            voiceState: s.sim.voiceState,
            mood: s.sim.mood,
          },
          null,
          2,
        ),
      );
    },
  });

  mc.registerTool({
    name: 'get_shader',
    description:
      'Read the orb\'s shaders for editing: the fragment GLSL body, the vertex shader (or the default sphere), the exact prelude that gets injected ABOVE your body (standard + declared uniform declarations), the declared uniforms, the current compile log, and the fragment size vs. its limit.',
    annotations: { readOnlyHint: true, idempotentHint: true },
    inputSchema: { type: 'object', properties: {} },
    execute: () => {
      const d = def();
      const prelude = buildFragmentSource(d).split('// — definition body —')[0];
      const declared = Object.fromEntries(
        Object.entries(d.uniforms).map(([name, u]) => [name, u.type]),
      );
      return ok(
        JSON.stringify(
          {
            fragment: d.shaders.fragment,
            vertex: d.shaders.vertex ?? '(default sphere vertex shader)',
            injectedPrelude: prelude,
            declaredUniforms: declared,
            compileLog: store().getSnapshot().shaderLog || '(clean)',
            fragmentChars: d.shaders.fragment.length,
            maxFragmentChars: MAX_FRAGMENT_CHARS,
          },
          null,
          2,
        ),
      );
    },
  });

  mc.registerTool({
    name: 'get_structure',
    description:
      'Read the full structure of the orb for editing: metadata, declared uniforms, controls (fields), bindings (with their index, for remove_binding), palettes, and the default palette.',
    annotations: { readOnlyHint: true, idempotentHint: true },
    inputSchema: { type: 'object', properties: {} },
    execute: () => {
      const d = def();
      return ok(
        JSON.stringify(
          {
            meta: {
              name: d.name,
              id: d.id,
              description: d.description ?? null,
              author: d.author ?? null,
              engine: d.engine,
            },
            uniforms: d.uniforms,
            fields: d.fields,
            bindings: d.bindings.map((b, index) => ({ index, ...b })),
            palettes: d.palettes,
            defaultPalette: d.defaultPalette,
          },
          null,
          2,
        ),
      );
    },
  });

  mc.registerTool({
    name: 'get_authoring_guide',
    description:
      'Read the orb authoring contract: how the fragment body is assembled, the standard uniforms and varyings available, how to declare uniforms and controls, the binding signals/ops/curves, naming rules, and limits. Call this once before writing shaders or bindings.',
    annotations: { readOnlyHint: true, idempotentHint: true },
    inputSchema: { type: 'object', properties: {} },
    execute: () =>
      ok(
        JSON.stringify(
          {
            engine: 'raymarch-v1',
            fragmentBodyContract:
              'Your fragment GLSL is appended after the injected prelude (standard uniforms + your declared uniforms). Write `void main()` and set `gl_FragColor`. Convention: ray origin = uLocalCamPos, ray dir = normalize(vLocalPosition - uLocalCamPos). The mesh is a sphere shell; R3F handles auto-rotate and drag.',
            standardUniforms: {
              uTime: 'float — seconds',
              uLocalCamPos: 'vec3 — camera position in local space (ray origin)',
              uPrimaryColor: 'vec3 — state/palette primary color',
              uSecondaryColor: 'vec3 — state/palette secondary color',
              uClickDir: 'vec3 — last click direction',
              uClickStrength: 'float — click bloom 0..1',
            },
            varyings: {
              vLocalPosition: 'vec3 — local-space surface position',
              vNormal: 'vec3 — normal',
              vViewPosition: 'vec3 — view-space position',
            },
            uniforms: {
              types: UNIFORM_TYPES,
              naming: 'must match ^u[A-Za-z0-9_]{1,63}$ (start with "u"); cannot shadow a standard uniform',
              note: 'color is a vec3 in GLSL whose default is a #rrggbb hex string. Declare a uniform (add_uniform) before referencing it in the shader.',
            },
            controls:
              'add_control creates a user-facing slider or color in one move: a uniform u<Key>, a settings field, a palette entry in every palette, and a param.<key> binding into the uniform. Use this for anything the user should be able to tweak.',
            bindings: {
              purpose:
                'Drive a uniform from a runtime signal each frame: the accumulator starts at the uniform default, value = curve(signal) * scale + offset, then acc = acc <op> value.',
              scalarSignals: SCALAR_SIGNALS,
              colorSignals: COLOR_SIGNALS,
              ops: BINDING_OPS,
              curves: BINDING_CURVES,
              colorRule: 'color signals require op "set"',
              reservedTargets: [...RESERVED_BINDING_TARGETS],
              targetComponents: 'append .x/.y/.z/.w to bind a single vector component, e.g. "uColorPhases.x"',
            },
            idRule: 'lowercase slug ^[a-z0-9][a-z0-9-]{1,63}$',
            limits: {
              maxFragmentChars: MAX_FRAGMENT_CHARS,
              maxDefinitionChars: MAX_DEFINITION_CHARS,
              maxUniforms: 64,
              maxBindings: 128,
              maxFields: 64,
              maxPalettes: 32,
            },
            workflow:
              'get_shader + get_structure to see the current orb → add_uniform for any new uniform → set_fragment_shader (returns the compiler log on failure; iterate) → add_control / add_binding to make it interactive → export_orb to get the finished .orbis.',
          },
          null,
          2,
        ),
      ),
  });
}

// ─────────────────────── live preview (values, no compile) ───────────────────────

function registerLiveTools(mc: ModelContextLike): void {
  mc.registerTool({
    name: 'set_control',
    description:
      "Set one of the orb's user-facing controls to a value (the live, previewed value — not a structural edit). Sliders take a number (clamped to range); colors take a #rrggbb hex. Use get_orb_state to list control keys and ranges.",
    annotations: { idempotentHint: true },
    inputSchema: {
      type: 'object',
      properties: {
        key: { type: 'string', description: 'Control key (see get_orb_state)' },
        value: { description: 'A number for sliders, or a #rrggbb hex for colors' },
      },
      required: ['key', 'value'],
    },
    execute: (args) => {
      const key = String(args.key ?? '');
      const field = def().fields.find((f) => f.key === key);
      if (!field)
        return err(`No control "${key}". Controls: ${def().fields.map((f) => f.key).join(', ') || '(none)'}`);
      if (field.kind === 'color') {
        const v = String(args.value ?? '').trim();
        if (!/^#[0-9a-fA-F]{6}$/.test(v)) return err(`"${v}" is not a #rrggbb hex color.`);
        store().setParam(key, v);
        return ok(`Set ${field.label} to ${v}.`);
      }
      const n = Number(args.value);
      if (!Number.isFinite(n)) return err(`"${String(args.value)}" is not a number.`);
      const v = clamp(n, field.min, field.max);
      store().setParam(key, v);
      return ok(`Set ${field.label} to ${v}${v !== n ? ` (clamped to ${field.min}..${field.max})` : ''}.`);
    },
  });

  mc.registerTool({
    name: 'set_palette',
    description: 'Switch the orb to one of its named palettes. Use get_orb_state for the names.',
    inputSchema: {
      type: 'object',
      properties: { name: { type: 'string', description: 'Palette name' } },
      required: ['name'],
    },
    execute: (args) => {
      const name = String(args.name ?? '');
      if (!def().palettes[name])
        return err(`No palette "${name}". Available: ${Object.keys(def().palettes).join(', ')}`);
      store().setPalette(name);
      return ok(`Palette set to "${name}".`);
    },
  });

  mc.registerTool({
    name: 'save_palette',
    description:
      'Save the current control values as a named palette — creates it if new, overwrites if it exists.',
    annotations: { destructiveHint: true },
    inputSchema: {
      type: 'object',
      properties: { name: { type: 'string', description: 'Palette name to save into' } },
      required: ['name'],
    },
    execute: (args) => {
      const name = String(args.name ?? '').trim();
      if (!name) return err('Provide a non-empty palette name.');
      store().savePalette(name);
      return ok(`Saved current values as palette "${name}".`);
    },
  });

  mc.registerTool({
    name: 'set_mood',
    description:
      'Set the previewed emotional mood that drives the orb. valence -1..1 (down..up), arousal -1..1 (calm..energetic), guardedness 0..1 (open..guarded). Omit a field to leave it unchanged.',
    inputSchema: {
      type: 'object',
      properties: {
        valence: { type: 'number', minimum: -1, maximum: 1 },
        arousal: { type: 'number', minimum: -1, maximum: 1 },
        guardedness: { type: 'number', minimum: 0, maximum: 1 },
      },
    },
    execute: (args) => {
      const patch: { valence?: number; arousal?: number; guardedness?: number } = {};
      if (args.valence != null) patch.valence = clamp(Number(args.valence), -1, 1);
      if (args.arousal != null) patch.arousal = clamp(Number(args.arousal), -1, 1);
      if (args.guardedness != null) patch.guardedness = clamp(Number(args.guardedness), 0, 1);
      if (Object.keys(patch).length === 0)
        return err('Provide at least one of valence, arousal, guardedness.');
      store().setMood(patch);
      return ok(`Mood set: ${JSON.stringify(store().getSnapshot().sim.mood)}.`);
    },
  });

  mc.registerTool({
    name: 'preview_voice_state',
    description:
      "Preview the orb in a voice state: 'idle', 'listening', 'thinking', or 'speaking'. Set animate=true to pulse the matching audio level (off by default to avoid a flashing preview).",
    inputSchema: {
      type: 'object',
      properties: {
        state: { type: 'string', enum: [...VOICE_STATES], description: 'Voice state to preview' },
        animate: { type: 'boolean', description: 'Pulse the audio level for listening/speaking' },
      },
      required: ['state'],
    },
    execute: (args) => {
      const state = String(args.state) as VoiceState;
      if (!VOICE_STATES.includes(state))
        return err(`Unknown state "${state}". One of: ${VOICE_STATES.join(', ')}.`);
      const animate = args.animate === true;
      store().setSim({
        voiceState: state,
        botMode: animate && state === 'speaking' ? 'pulse' : 'off',
        userMode: animate && state === 'listening' ? 'pulse' : 'off',
      });
      return ok(`Previewing "${state}"${animate ? ' (animated)' : ''}.`);
    },
  });
}

// ───────────────────────────── shader authoring ─────────────────────────────

function registerShaderTools(mc: ModelContextLike): void {
  mc.registerTool({
    name: 'set_fragment_shader',
    description:
      'Replace the fragment shader BODY (the GLSL appended after the injected prelude — see get_authoring_guide / get_shader). It is compiled against real WebGL; on a compile error nothing changes and the compiler log is returned so you can fix and retry. Must define void main() and write gl_FragColor.',
    inputSchema: {
      type: 'object',
      properties: { glsl: { type: 'string', description: 'Fragment shader body GLSL' } },
      required: ['glsl'],
    },
    execute: (args) => {
      const fragment = String(args.glsl ?? '');
      if (!fragment.trim()) return err('glsl is empty.');
      if (fragment.length > MAX_FRAGMENT_CHARS)
        return err(`Fragment exceeds ${MAX_FRAGMENT_CHARS} chars.`);
      const d = def();
      return commit(
        { ...d, shaders: { ...d.shaders, fragment } },
        'Fragment shader updated and compiled clean.',
        { compile: true },
      );
    },
  });

  mc.registerTool({
    name: 'set_vertex_shader',
    description:
      'Replace the vertex shader. Pass null or an empty string to reset to the default sphere vertex shader. Compiled before it is applied.',
    inputSchema: {
      type: 'object',
      properties: { glsl: { description: 'Vertex shader GLSL, or null for the default sphere' } },
      required: ['glsl'],
    },
    execute: (args) => {
      const raw = args.glsl;
      const vertex = raw == null || String(raw).trim() === '' ? null : String(raw);
      const d = def();
      return commit(
        { ...d, shaders: { ...d.shaders, vertex } },
        vertex ? 'Vertex shader set.' : 'Vertex shader reset to the default sphere.',
        { compile: true },
      );
    },
  });
}

// ───────────────────────── structure (uniforms / controls / bindings / meta) ─────────────────────────

function registerStructureTools(mc: ModelContextLike): void {
  mc.registerTool({
    name: 'add_uniform',
    description:
      'Declare a new uniform so the fragment shader can reference it. Name must start with "u" (^u[A-Za-z0-9_]{1,63}$). type: float | vec2 | vec3 | vec4 | color. Optional default: number (float), number[] (vec*), or #rrggbb (color).',
    inputSchema: {
      type: 'object',
      properties: {
        name: { type: 'string', description: 'Uniform name, e.g. uGlow' },
        type: { type: 'string', enum: [...UNIFORM_TYPES] },
        default: { description: 'number (float), array of numbers (vec*), or #rrggbb (color)' },
      },
      required: ['name', 'type'],
    },
    execute: (args) => {
      const name = String(args.name ?? '');
      const type = String(args.type ?? '');
      if (!(UNIFORM_TYPES as readonly string[]).includes(type))
        return err(`type must be one of ${UNIFORM_TYPES.join(' | ')}.`);
      const d = def();
      if (d.uniforms[name]) return err(`Uniform "${name}" already exists.`);
      const decl: { type: string; default?: unknown } = { type };
      if (args.default !== undefined) decl.default = args.default;
      return commit(
        { ...d, uniforms: { ...d.uniforms, [name]: decl as OrbDefinition['uniforms'][string] } },
        `Added uniform ${name} (${type}). Reference it in the fragment shader, or bind a signal into it with add_binding.`,
      );
    },
  });

  mc.registerTool({
    name: 'remove_uniform',
    description:
      'Remove a declared uniform and any bindings that target it. Rejected (with the compiler log) if the fragment shader still references it.',
    annotations: { destructiveHint: true },
    inputSchema: {
      type: 'object',
      properties: { name: { type: 'string' } },
      required: ['name'],
    },
    execute: (args) => {
      const name = String(args.name ?? '');
      const d = def();
      if (!d.uniforms[name]) return err(`No uniform "${name}".`);
      const uniforms = { ...d.uniforms };
      delete uniforms[name];
      const bindings = d.bindings.filter((b) => b.target.split('.')[0] !== name);
      return commit(
        { ...d, uniforms, bindings },
        `Removed uniform ${name}${bindings.length !== d.bindings.length ? ' and its bindings' : ''}.`,
        { compile: true },
      );
    },
  });

  mc.registerTool({
    name: 'add_control',
    description:
      'Add a user-facing control in one move: a uniform u<Key>, a settings field, a palette entry in every palette, and a param.<key> binding into the uniform. kind: "slider" (float) or "color". This is how you expose something for the user to tweak.',
    inputSchema: {
      type: 'object',
      properties: {
        key: { type: 'string', description: 'Param key, ^[a-zA-Z][a-zA-Z0-9]{0,40}$' },
        kind: { type: 'string', enum: ['slider', 'color'] },
        label: { type: 'string', description: 'Display label (defaults to key)' },
        section: { type: 'string', enum: [...SECTIONS], description: 'Settings section (default energy)' },
        min: { type: 'number', description: 'slider only (default 0)' },
        max: { type: 'number', description: 'slider only (default 2)' },
        step: { type: 'number', description: 'slider only (default 0.05)' },
        default: { description: 'Initial value: number (slider) or #rrggbb (color)' },
      },
      required: ['key', 'kind'],
    },
    execute: (args) => {
      const key = String(args.key ?? '');
      if (!/^[a-zA-Z][a-zA-Z0-9]{0,40}$/.test(key))
        return err('key must match ^[a-zA-Z][a-zA-Z0-9]{0,40}$.');
      const kind = String(args.kind ?? '');
      if (kind !== 'slider' && kind !== 'color') return err('kind must be "slider" or "color".');
      const d = def();
      if (d.fields.some((f) => f.key === key)) return err(`A control "${key}" already exists.`);

      const uniform = `u${key[0].toUpperCase()}${key.slice(1)}`;
      if (d.uniforms[uniform]) return err(`Uniform ${uniform} already exists — pick another key.`);
      const section = (SECTIONS as readonly string[]).includes(String(args.section))
        ? (args.section as SectionId)
        : 'energy';
      const isColor = kind === 'color';
      const defaultValue = isColor
        ? typeof args.default === 'string' && /^#[0-9a-fA-F]{6}$/.test(args.default)
          ? args.default
          : '#9b87f2'
        : Number.isFinite(Number(args.default))
          ? Number(args.default)
          : 1.0;
      const min = Number.isFinite(Number(args.min)) ? Number(args.min) : 0;
      const max = Number.isFinite(Number(args.max)) ? Number(args.max) : 2;
      const step = Number.isFinite(Number(args.step)) ? Number(args.step) : 0.05;

      const field = isColor
        ? ({ kind: 'color', key, label: String(args.label ?? key), section } as const)
        : ({ kind: 'slider', key, label: String(args.label ?? key), section, min, max, step } as const);

      const next: OrbDefinition = {
        ...d,
        uniforms: {
          ...d.uniforms,
          [uniform]: { type: isColor ? 'color' : 'float', default: defaultValue },
        },
        fields: [...d.fields, field],
        palettes: Object.fromEntries(
          Object.entries(d.palettes).map(([name, p]) => [name, { ...p, [key]: defaultValue }]),
        ),
        bindings: [...d.bindings, { target: uniform, signal: `param.${key}` }],
      };
      const result = commit(next, `Added ${kind} control "${key}" → uniform ${uniform}.`);
      if (!result.isError) store().setParam(key, defaultValue);
      return result;
    },
  });

  mc.registerTool({
    name: 'remove_control',
    description:
      'Remove a user-facing control: drops the field, its palette entries, and its param.<key> binding. The underlying uniform is left in place (the shader may still use it) — use remove_uniform to delete it.',
    annotations: { destructiveHint: true },
    inputSchema: {
      type: 'object',
      properties: { key: { type: 'string' } },
      required: ['key'],
    },
    execute: (args) => {
      const key = String(args.key ?? '');
      const d = def();
      if (!d.fields.some((f) => f.key === key)) return err(`No control "${key}".`);
      const palettes = Object.fromEntries(
        Object.entries(d.palettes).map(([name, p]) => {
          const { [key]: _drop, ...rest } = p as Record<string, number | string>;
          return [name, rest];
        }),
      );
      return commit(
        {
          ...d,
          fields: d.fields.filter((f) => f.key !== key),
          palettes,
          bindings: d.bindings.filter((b) => b.signal !== `param.${key}`),
        },
        `Removed control "${key}".`,
      );
    },
  });

  mc.registerTool({
    name: 'add_binding',
    description:
      'Wire a runtime signal into a uniform each frame (this is what makes the orb react to voice, mood, time, or a control). See get_authoring_guide for valid signals/ops/curves. Cannot target a reserved uniform.',
    inputSchema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Uniform name, optionally with .x/.y/.z/.w' },
        signal: { type: 'string', description: 'e.g. bot.level, mood.arousal, time, param.<key>' },
        op: { type: 'string', enum: [...BINDING_OPS], description: 'default set' },
        scale: { type: 'number', description: 'default 1' },
        offset: { type: 'number', description: 'default 0' },
        curve: { type: 'string', enum: [...BINDING_CURVES], description: 'default linear' },
        smooth: { type: 'number', description: 'one-pole smoothing (0,1]; lower = smoother' },
      },
      required: ['target', 'signal'],
    },
    execute: (args) => {
      const binding: Binding = {
        target: String(args.target ?? ''),
        signal: String(args.signal ?? ''),
      };
      if (args.op != null) binding.op = args.op as Binding['op'];
      if (args.scale != null) binding.scale = Number(args.scale);
      if (args.offset != null) binding.offset = Number(args.offset);
      if (args.curve != null) binding.curve = args.curve as Binding['curve'];
      if (args.smooth != null) binding.smooth = Number(args.smooth);
      const d = def();
      // validateOrbDefinition is the authoritative gate (target exists, signal
      // valid, color-signal→set, reserved targets, etc.).
      return commit(
        { ...d, bindings: [...d.bindings, binding] },
        `Bound ${binding.signal} → ${binding.target} (op ${binding.op ?? 'set'}).`,
      );
    },
  });

  mc.registerTool({
    name: 'remove_binding',
    description: 'Remove a binding by its index (see get_structure for indices).',
    annotations: { destructiveHint: true },
    inputSchema: {
      type: 'object',
      properties: { index: { type: 'integer', minimum: 0 } },
      required: ['index'],
    },
    execute: (args) => {
      const idx = Number(args.index);
      const d = def();
      if (!Number.isInteger(idx) || idx < 0 || idx >= d.bindings.length)
        return err(`index must be 0..${d.bindings.length - 1}.`);
      const removed = d.bindings[idx];
      return commit(
        { ...d, bindings: d.bindings.filter((_, i) => i !== idx) },
        `Removed binding ${idx} (${removed.signal} → ${removed.target}).`,
      );
    },
  });

  mc.registerTool({
    name: 'set_meta',
    description:
      "Edit the orb's metadata. id must be a lowercase slug (^[a-z0-9][a-z0-9-]{1,63}$); name ≤120 chars. Omit a field to leave it unchanged.",
    inputSchema: {
      type: 'object',
      properties: {
        name: { type: 'string' },
        id: { type: 'string', description: 'lowercase slug' },
        description: { type: 'string' },
        author: { type: 'string' },
      },
    },
    execute: (args) => {
      const d = def();
      const next: OrbDefinition = { ...d };
      if (args.name != null) next.name = String(args.name);
      if (args.id != null) next.id = String(args.id);
      if (args.description != null) next.description = String(args.description);
      if (args.author != null) next.author = String(args.author);
      return commit(next, 'Metadata updated.');
    },
  });
}

// ───────────────────────────── import / export ─────────────────────────────

function registerIoTools(mc: ModelContextLike): void {
  mc.registerTool({
    name: 'export_orb',
    description: 'Return the complete current orb as a .orbis JSON string (to save or hand to the user).',
    annotations: { readOnlyHint: true, idempotentHint: true },
    inputSchema: { type: 'object', properties: {} },
    execute: () => ok(JSON.stringify(def(), null, 2)),
  });

  mc.registerTool({
    name: 'load_orb',
    description:
      'Replace the entire orb with a complete .orbis definition (a JSON object or string). Validated and compiled before it is applied; the previous draft is discarded.',
    annotations: { destructiveHint: true },
    inputSchema: {
      type: 'object',
      properties: {
        orbis: { description: 'A full .orbis definition, as a JSON object or stringified JSON' },
      },
      required: ['orbis'],
    },
    execute: (args) => {
      let raw: unknown = args.orbis;
      if (typeof raw === 'string') {
        try {
          raw = JSON.parse(raw);
        } catch {
          return err('orbis was a string but not valid JSON.');
        }
      }
      const res = validateOrbDefinition(raw);
      if (!res.ok) return err(`Invalid .orbis: ${res.errors.slice(0, 8).join('; ')}`);
      const c = compileCheck(res.def);
      if (!c.ok) return err(`Orb shader did not compile — not loaded:\n${c.log}`);
      store().loadDefinition(res.def);
      store().setShaderLog('');
      return ok(`Loaded orb "${res.def.name}" (${res.def.id}).`);
    },
  });
}
