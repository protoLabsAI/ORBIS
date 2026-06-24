/**
 * WebMCP tool surface for the orb editor.
 *
 * Exposes the editor's capabilities as callable tools on the W3C WebMCP
 * runtime (`document.modelContext` / `navigator.modelContext`, polyfilled by
 * `@mcp-b/global`). An AI agent — Chrome's built-in agent, the MCP-B
 * inspector, or ORBIS driving an embedded webview — can then edit the orb by
 * calling typed tools instead of synthesizing clicks on the DOM.
 *
 * The page is a backendless SPA with no secrets: every tool is a pure
 * client-state mutation on the editor `store`. That is why we accept the
 * library default transport (`allowedOrigins: ['*']`) — the worst a connected
 * agent can do is change the local orb preview. Tightening the origin list is
 * a follow-up only if the editor is ever embedded by untrusted third parties.
 *
 * Per-field `set_<key>` tools are generated from the orb's `fields` schema, so
 * adding a control automatically yields a tool — no hand-maintained list. They
 * are re-synced when the definition changes (template pick / `.orbis` import)
 * via the spec-current `AbortSignal` unregister pattern (`unregisterTool` was
 * removed from the WebMCP spec in April 2026).
 */
import { initializeWebModelContext } from '@mcp-b/global';
// Importing a type from this package also activates its ambient `declare
// global` that types `document.modelContext` / `navigator.modelContext`.
import type { ToolDescriptor } from '@mcp-b/webmcp-types';
import { validateOrbDefinition, type FieldSpec, type VoiceState } from '@orbis/orb-runtime';
import { store } from './state';

const VOICE_STATES: readonly VoiceState[] = ['idle', 'listening', 'thinking', 'speaking'];

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

/** Register every editor tool and keep the per-field tools in sync. */
export function registerEditorTools(): void {
  // Idempotent — the auto-init on import has usually already run. Calling it
  // here also pins the import so the runtime is never tree-shaken away.
  initializeWebModelContext();

  const mc = document.modelContext as unknown as ModelContextLike | undefined;
  if (!mc) {
    console.warn('[webmcp] document.modelContext unavailable — agent tools disabled');
    return;
  }

  registerStaticTools(mc);
  syncFieldTools(mc);
  // Field tools change only on structural edits; param scrubbing is a no-op
  // here (the signature guard bails), so subscribing per-commit is cheap.
  store().subscribe(() => syncFieldTools(mc));
}

function registerStaticTools(mc: ModelContextLike): void {
  mc.registerTool({
    name: 'get_orb_state',
    description:
      'Read the orb currently under edit: its name, the active palette and all available palettes, every editable control (key, label, kind, numeric range) with its current value, and the previewed voice state and mood. Call this first to learn what you can change.',
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
    name: 'set_palette',
    description:
      'Switch the orb to one of its named color palettes. Use get_orb_state to list the available palette names.',
    inputSchema: {
      type: 'object',
      properties: { name: { type: 'string', description: 'Palette name' } },
      required: ['name'],
    },
    execute: (args) => {
      const name = String(args.name ?? '');
      const def = store().getSnapshot().definition;
      if (!def.palettes[name])
        return err(`No palette "${name}". Available: ${Object.keys(def.palettes).join(', ')}`);
      store().setPalette(name);
      return ok(`Palette set to "${name}".`);
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
      if (Object.keys(patch).length === 0) return err('Provide at least one of valence, arousal, guardedness.');
      store().setMood(patch);
      return ok(`Mood set: ${JSON.stringify(store().getSnapshot().sim.mood)}.`);
    },
  });

  mc.registerTool({
    name: 'preview_voice_state',
    description:
      "Preview how the orb looks in a voice state: 'idle', 'listening', 'thinking', or 'speaking'. Set animate=true to pulse the matching audio level so the orb moves (off by default to avoid a flashing preview).",
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

  mc.registerTool({
    name: 'save_palette',
    description:
      'Save the current control values as a named palette — creates it if new, overwrites if it already exists.',
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
    name: 'load_orb',
    description:
      'Replace the entire orb under edit with a complete .orbis definition (a JSON object or a JSON string). The format is validated; the previous draft is discarded.',
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
      if (!res.ok) return err(`Invalid .orbis: ${res.errors.slice(0, 4).join('; ')}`);
      store().loadDefinition(res.def);
      return ok(`Loaded orb "${res.def.name}" (${res.def.id}).`);
    },
  });
}

// ── per-field tools (generated from the schema, re-synced on definition change) ──

let lastFieldSig = '';
const fieldControllers = new Map<string, AbortController>();

function fieldSignature(fields: FieldSpec[]): string {
  return fields
    .map((f) => (f.kind === 'slider' ? `${f.key}:s:${f.min}:${f.max}:${f.step}` : `${f.key}:c`))
    .join('|');
}

function syncFieldTools(mc: ModelContextLike): void {
  const fields = store().getSnapshot().definition.fields;
  const sig = fieldSignature(fields);
  if (sig === lastFieldSig) return; // params changed, not the control set — nothing to do
  lastFieldSig = sig;

  // Field sets are small and change rarely; tear down and rebuild wholesale.
  for (const ctrl of fieldControllers.values()) ctrl.abort();
  fieldControllers.clear();

  for (const field of fields) {
    const ctrl = new AbortController();
    mc.registerTool(toolForField(field), { signal: ctrl.signal });
    fieldControllers.set(field.key, ctrl);
  }
}

function toolForField(field: FieldSpec): ToolDescriptor {
  if (field.kind === 'color') {
    return {
      name: `set_${field.key}`,
      description: `Set the orb's "${field.label}" color (${field.section}). Value is a hex color like "#9b87f2".`,
      inputSchema: {
        type: 'object',
        properties: { value: { type: 'string', description: 'Hex color, e.g. #9b87f2' } },
        required: ['value'],
      },
      execute: (args) => {
        const v = String(args.value ?? '').trim();
        if (!/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(v))
          return err(`"${v}" is not a hex color — use #rgb or #rrggbb.`);
        store().setParam(field.key, v);
        return ok(`Set ${field.label} to ${v}.`);
      },
    };
  }
  return {
    name: `set_${field.key}`,
    description: `Set the orb's "${field.label}" (${field.section}). A number from ${field.min} to ${field.max}.`,
    annotations: { idempotentHint: true },
    inputSchema: {
      type: 'object',
      properties: {
        value: { type: 'number', minimum: field.min, maximum: field.max, description: `${field.min}..${field.max}` },
      },
      required: ['value'],
    },
    execute: (args) => {
      const n = Number(args.value);
      if (!Number.isFinite(n)) return err(`"${String(args.value)}" is not a number.`);
      const v = clamp(n, field.min, field.max);
      store().setParam(field.key, v);
      return ok(`Set ${field.label} to ${v}${v !== n ? ` (clamped to ${field.min}..${field.max})` : ''}.`);
    },
  };
}
