import { useCallback, useEffect, useState } from 'react';
import { Pencil, Plus, Radar, Trash2 } from 'lucide-react';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import {
  api,
  type Delegate,
  type DelegateFieldSpec,
  type DelegateTestResult,
  type DelegateTypeSpec,
  type DelegateWithStatus,
  type DiscoveredAgent,
} from '@/lib/api';

/**
 * Delegates settings — manage the sub-agents the orb can route to via the
 * LLM's `delegate_to` tool. The New/Edit form is rendered **generically** from
 * `GET /api/delegate-types` (each backend `DelegateAdapter` declares its
 * capabilities + field schema), so adding a delegate type needs no change
 * here — its tile + fields appear from the contract.
 *
 * No credentials cross the wire. The schema only references env-var *names*
 * (`credentialsEnv` / `api_key_env`); the actual values come from the process
 * environment at registry-parse time.
 */
export function DelegatesSettings() {
  const [items, setItems] = useState<DelegateWithStatus[] | null>(null);
  const [types, setTypes] = useState<DelegateTypeSpec[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditingState | null>(null);

  const reload = useCallback(async () => {
    try {
      const [list, typeList] = await Promise.all([
        api.delegates.list(),
        api.delegates.types(),
      ]);
      setItems(list.delegates);
      setTypes(typeList.types);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const onSaved = useCallback((next: DelegateWithStatus[]) => {
    setItems(next);
    setEditing(null);
  }, []);

  const onDelete = useCallback(async (name: string) => {
    // No fancy modal — settings is a power-user surface; the verbal
    // "are you sure?" confirms intent without a layout shift.
    if (!window.confirm(`Delete delegate ${name}?`)) return;
    try {
      const r = await api.delegates.remove(name);
      setItems(r.delegates);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  if ((items === null || types === null) && !error) {
    return (
      <Panel title="Delegates">
        <div className="text-xs text-fg-subtle">Loading…</div>
      </Panel>
    );
  }

  const typeSpecs = types ?? [];

  return (
    <Panel title="Delegates">
      <div className="space-y-3">
        <p className="text-sm text-fg-muted -mt-1">
          Sub-agents the orb can route to. The LLM picks one based on its
          description when it decides delegation is the right call.
        </p>

        {error && <div className="text-xs text-danger break-words">{error}</div>}

        {items && items.length === 0 && !editing && (
          <div className="text-xs text-fg-subtle italic">
            No delegates configured yet.
          </div>
        )}

        {items && items.length > 0 && (
          <ul className="space-y-2">
            {items.map((entry) => (
              <li
                key={entry.name}
                className="flex items-start gap-3 rounded-md border border-edge bg-raised/40 p-2.5"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-fg font-mono truncate">
                      {entry.name}
                    </span>
                    <TypeBadge type={entry.type} specs={typeSpecs} />
                    {!entry.configured && (
                      <span
                        className="text-helper uppercase tracking-wider text-brand"
                        title="Runtime registry rejected this entry — fix the config or it won't be available to the LLM."
                      >
                        ⚠ unconfigured
                      </span>
                    )}
                  </div>
                  <div className="text-sm text-fg-muted mt-0.5 line-clamp-2">
                    {entry.description}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setEditing({ kind: 'edit', original: entry })}
                    className="p-1.5 rounded text-fg-muted hover:bg-edge hover:text-fg-body"
                    aria-label={`Edit ${entry.name}`}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onDelete(entry.name)}
                    className="p-1.5 rounded text-fg-muted hover:bg-red-900/40 hover:text-danger"
                    aria-label={`Delete ${entry.name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {editing ? (
          <DelegateEditor
            state={editing}
            specs={typeSpecs}
            onCancel={() => setEditing(null)}
            onSaved={onSaved}
          />
        ) : (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setEditing({ kind: 'create' })}
            disabled={typeSpecs.length === 0}
          >
            <Plus className="h-3.5 w-3.5 mr-1" />
            Add delegate
          </Button>
        )}

        {!editing && (
          <DiscoverSection
            onAdd={(agent) =>
              setEditing({
                kind: 'create',
                prefill: {
                  type: 'a2a',
                  name: slugify(agent.name),
                  description: agent.description,
                  url: `${agent.url}/a2a`,
                },
              })
            }
          />
        )}
      </div>
    </Panel>
  );
}

/** Delegate names feed delegate_to(target=...) — lowercase, no spaces. */
function slugify(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
}

/**
 * Network discovery — surface A2A agents broadcasting nearby (protoAgent's
 * mDNS `_protoagent._tcp` adverts + local/tailnet card probes) and add one
 * as an a2a delegate with the editor prefilled from its agent card.
 */
function DiscoverSection({ onAdd }: { onAdd: (agent: DiscoveredAgent) => void }) {
  const [scanning, setScanning] = useState(false);
  const [found, setFound] = useState<DiscoveredAgent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onScan = useCallback(async () => {
    setScanning(true);
    setError(null);
    try {
      const r = await api.delegates.discover();
      setFound(r.discovered);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setScanning(false);
    }
  }, []);

  return (
    <div className="pt-2 border-t border-edge space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-xs uppercase tracking-wider text-fg-subtle">
            Discover on your network
          </div>
          <div className="text-xs text-fg-muted mt-0.5">
            Finds protoLabs agents broadcasting on this machine, the LAN, and
            your tailnet.
          </div>
        </div>
        <Button variant="secondary" size="sm" onClick={onScan} disabled={scanning}>
          <Radar className="h-3.5 w-3.5 mr-1" />
          {scanning ? 'Scanning…' : 'Scan'}
        </Button>
      </div>

      {error && <div className="text-xs text-danger break-words">{error}</div>}

      {found && found.length === 0 && !scanning && (
        <div className="text-xs text-fg-subtle italic">
          No new agents broadcasting right now.
        </div>
      )}

      {found && found.length > 0 && (
        <ul className="space-y-2">
          {found.map((agent) => (
            <li
              key={`${agent.host}:${agent.port}`}
              className="flex items-start gap-3 rounded-md border border-edge bg-raised/40 p-2.5"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-fg font-mono truncate">{agent.name}</span>
                  <span className="text-helper text-fg-subtle font-mono">
                    {agent.host}:{agent.port}
                  </span>
                </div>
                {agent.description && (
                  <div className="text-sm text-fg-muted mt-0.5 line-clamp-2">
                    {agent.description}
                  </div>
                )}
              </div>
              <Button
                variant="secondary"
                size="sm"
                className="shrink-0"
                onClick={() => onAdd(agent)}
              >
                <Plus className="h-3.5 w-3.5 mr-1" />
                Add
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dotted-path get/set — a field's `key` ("auth.scheme") maps to a nested path.
// ---------------------------------------------------------------------------

type Draft = Record<string, unknown>;

function getPath(obj: Draft, path: string): unknown {
  return path
    .split('.')
    .reduce<unknown>(
      (o, k) => (o && typeof o === 'object' ? (o as Draft)[k] : undefined),
      obj,
    );
}

function setPath(obj: Draft, path: string, value: unknown): Draft {
  const keys = path.split('.');
  const next: Draft = { ...obj };
  let cur = next;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    const child = cur[k];
    cur[k] = child && typeof child === 'object' ? { ...(child as Draft) } : {};
    cur = cur[k] as Draft;
  }
  cur[keys[keys.length - 1]] = value;
  return next;
}

/** Fresh draft for a type: name/description carried by the caller, every field
 *  seeded from its schema `default`. */
function blankDraftFor(spec: DelegateTypeSpec, common: { name: string; description: string }): Draft {
  let d: Draft = { name: common.name, description: common.description, type: spec.type };
  for (const f of spec.fields) {
    if (f.default !== undefined) d = setPath(d, f.key, f.default);
  }
  return d;
}

// ---------------------------------------------------------------------------
// Editor (create + edit share the form; only the submit verb differs)
// ---------------------------------------------------------------------------

type EditingState =
  | { kind: 'create'; prefill?: Draft }
  | { kind: 'edit'; original: DelegateWithStatus };

interface EditorProps {
  state: EditingState;
  specs: DelegateTypeSpec[];
  onCancel: () => void;
  onSaved: (next: DelegateWithStatus[]) => void;
}

function DelegateEditor({ state, specs, onCancel, onSaved }: EditorProps) {
  const isEdit = state.kind === 'edit';
  const [draft, setDraft] = useState<Draft>(() => {
    if (isEdit) return { ...state.original } as Draft;
    // Discovery's Add prefills type/name/description/url; field defaults
    // from the type's schema fill the rest.
    const prefill = state.prefill ?? {};
    const spec = specs.find((s) => s.type === prefill.type) ?? specs[0];
    return {
      ...blankDraftFor(spec, {
        name: String(prefill.name ?? ''),
        description: String(prefill.description ?? ''),
      }),
      ...prefill,
    };
  });
  const [test, setTest] = useState<DelegateTestResult | 'checking' | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeSpec = specs.find((s) => s.type === draft.type) ?? specs[0];

  const setField = useCallback((key: string, value: unknown) => {
    setDraft((prev) => setPath(prev, key, value));
    setTest(null);
  }, []);

  const onSwitchType = useCallback(
    (nextType: string) => {
      const spec = specs.find((s) => s.type === nextType);
      if (!spec) return;
      // Reset type-specific fields to their defaults but keep name + description.
      setDraft((prev) =>
        blankDraftFor(spec, {
          name: String(prev.name ?? ''),
          description: String(prev.description ?? ''),
        }),
      );
      setTest(null);
    },
    [specs],
  );

  const onTest = useCallback(async () => {
    setError(null);
    setTest('checking');
    try {
      const r = await api.delegates.test(draft as unknown as Delegate);
      setTest(r);
    } catch (e) {
      setTest({ ok: false, error: (e as Error).message });
    }
  }, [draft]);

  const onSave = useCallback(async () => {
    setError(null);
    setSaving(true);
    try {
      const entry = draft as unknown as Delegate;
      const r = isEdit
        ? await api.delegates.update(state.original.name, entry)
        : await api.delegates.create(entry);
      onSaved(r.delegates);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }, [draft, isEdit, onSaved, state]);

  return (
    <div className="rounded-md border border-brand/30 bg-brand/5 p-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wider text-brand/80">
          {isEdit ? `Edit ${state.original.name}` : 'New delegate'}
        </span>
        <button
          type="button"
          onClick={onCancel}
          className="text-xs text-fg-subtle hover:text-fg-body"
        >
          Cancel
        </button>
      </div>

      {/* Type picker — tiles from the registered types. Switching type resets
          the type-specific fields to their defaults but keeps name + description. */}
      <div
        className="grid gap-2"
        style={{ gridTemplateColumns: `repeat(${Math.min(specs.length, 3)}, minmax(0, 1fr))` }}
      >
        {specs.map((s) => (
          <TypeTile
            key={s.type}
            active={draft.type === s.type}
            label={s.label || s.type}
            blurb={s.blurb}
            onClick={() => onSwitchType(s.type)}
          />
        ))}
      </div>

      <Field label="Name" hint="Used in delegate_to(target=...). Lowercase, no spaces.">
        <input
          value={String(draft.name ?? '')}
          onChange={(e) => setField('name', e.target.value)}
          disabled={isEdit}
          placeholder="ava"
          className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-muted font-mono disabled:opacity-50"
          spellCheck={false}
        />
      </Field>

      <Field
        label="Description"
        hint="The LLM reads this to pick between delegates — be specific."
      >
        <input
          value={String(draft.description ?? '')}
          onChange={(e) => setField('description', e.target.value)}
          placeholder="Chief of staff — sitreps, planning, fleet delegation."
          className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-muted"
          spellCheck={false}
        />
      </Field>

      {/* Type-specific fields, driven entirely by the schema. */}
      {activeSpec?.fields.map((f) => (
        <DelegateField
          key={f.key}
          field={f}
          value={getPath(draft, f.key)}
          onChange={(v) => setField(f.key, v)}
        />
      ))}

      {/* Test + save row. Probe works for every type (a2a card / openai ping /
          acp PATH+workdir), so Test is available across the board. */}
      <div className="flex items-center gap-2 pt-1">
        <Button variant="secondary" size="sm" onClick={onTest} disabled={test === 'checking'}>
          {test === 'checking' ? 'Testing…' : 'Test'}
        </Button>
        <Button size="sm" onClick={onSave} disabled={saving}>
          {saving ? 'Saving…' : isEdit ? 'Save changes' : 'Create'}
        </Button>
        {test && test !== 'checking' && (
          test.ok ? (
            <span className="text-xs text-success">
              ✓ Reachable{test.latency_ms !== undefined && ` (${test.latency_ms} ms)`}
            </span>
          ) : (
            <span className="text-xs text-danger truncate max-w-[55%]">
              ✗ {test.error ?? 'unknown error'}
            </span>
          )
        )}
      </div>

      {error && <div className="text-xs text-danger">{error}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Generic field renderer — one input per FieldSpec.kind.
// ---------------------------------------------------------------------------

const INPUT_CLS =
  'w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-muted font-mono';

function DelegateField({
  field,
  value,
  onChange,
}: {
  field: DelegateFieldSpec;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const label = field.required ? field.label : `${field.label} (optional)`;

  if (field.kind === 'select') {
    return (
      <Field label={label} hint={field.help}>
        <select
          value={String(value ?? '')}
          // Empty option = "none" → omit the key entirely (undefined).
          onChange={(e) => onChange(e.target.value || undefined)}
          className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body font-mono"
        >
          {(field.options ?? []).map((opt) => (
            <option key={opt} value={opt}>
              {opt === '' ? '(none)' : opt}
            </option>
          ))}
        </select>
      </Field>
    );
  }

  if (field.kind === 'textarea') {
    return (
      <Field label={label} hint={field.help}>
        <textarea
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          rows={2}
          className="w-full rounded-md border border-edge bg-raised/60 px-2.5 py-1.5 text-xs text-fg-body placeholder-fg-muted leading-snug"
          spellCheck={false}
        />
      </Field>
    );
  }

  if (field.kind === 'args') {
    const arr = Array.isArray(value) ? (value as string[]) : [];
    return (
      <Field label={label} hint={field.help}>
        <input
          value={arr.join(' ')}
          onChange={(e) => onChange(e.target.value.split(/\s+/).filter(Boolean))}
          placeholder={field.placeholder}
          className={INPUT_CLS}
          spellCheck={false}
        />
      </Field>
    );
  }

  if (field.kind === 'number') {
    return (
      <Field label={label} hint={field.help}>
        <input
          type="number"
          value={value === undefined || value === null ? '' : String(value)}
          onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))}
          placeholder={field.placeholder}
          className={INPUT_CLS}
        />
      </Field>
    );
  }

  // text | secret-env | path — all plain single-line inputs.
  return (
    <Field label={label} hint={field.help}>
      <input
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.placeholder}
        className={INPUT_CLS}
        spellCheck={false}
      />
    </Field>
  );
}

// ---------------------------------------------------------------------------
// Tiny presentation helpers
// ---------------------------------------------------------------------------

function TypeBadge({ type, specs }: { type: string; specs: DelegateTypeSpec[] }) {
  const spec = specs.find((s) => s.type === type);
  const label = spec?.label || type.toUpperCase();
  return (
    <span className="text-helper uppercase tracking-wider text-fg-subtle border border-edge rounded px-1.5 py-0.5">
      {label}
    </span>
  );
}

function TypeTile({
  active,
  label,
  blurb,
  onClick,
}: {
  active: boolean;
  label: string;
  blurb: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        'p-2 text-left rounded-md border transition-colors ' +
        (active
          ? 'border-brand/60 bg-brand/10'
          : 'border-edge bg-raised/40 hover:bg-raised/70')
      }
    >
      <div className="text-sm text-fg">{label}</div>
      <div className="text-xs text-fg-muted mt-0.5">{blurb}</div>
    </button>
  );
}
