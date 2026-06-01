import { useCallback, useEffect, useState } from 'react';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import {
  api,
  type Delegate,
  type DelegateA2A,
  type DelegateOpenAI,
  type DelegateTestResult,
  type DelegateWithStatus,
} from '@/lib/api';

/**
 * Delegates settings — manage the sub-agents the orb can route to via
 * the LLM's `delegate_to` tool. Mirrors LLMSettings posture: list view
 * with a slide-down editor; type-specific fields drive what's shown;
 * the Test button pings before save so users can verify reachability
 * without having to commit to the persisted YAML first.
 *
 * No credentials cross the wire. The schema only references env-var
 * *names* (``credentialsEnv`` / ``api_key_env``); the actual values
 * come from the process environment at registry-parse time. This is
 * the same posture as the rest of the settings panel — secrets stay
 * in .env, UI manipulates indirection.
 */
export function DelegatesSettings() {
  const [items, setItems] = useState<DelegateWithStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditingState | null>(null);

  const reload = useCallback(async () => {
    try {
      const r = await api.delegates.list();
      setItems(r.delegates);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const onSaved = useCallback(
    (next: DelegateWithStatus[]) => {
      setItems(next);
      setEditing(null);
    },
    [],
  );

  const onDelete = useCallback(
    async (name: string) => {
      // No fancy modal — settings is a power-user surface; the verbal
      // "are you sure?" confirms intent without a layout shift.
      if (!window.confirm(`Delete delegate ${name}?`)) return;
      try {
        const r = await api.delegates.remove(name);
        setItems(r.delegates);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [],
  );

  if (items === null && !error) {
    return (
      <Panel title="Delegates">
        <div className="text-xs text-fg-subtle">Loading…</div>
      </Panel>
    );
  }

  return (
    <Panel title="Delegates">
      <div className="space-y-3">
        <p className="text-sm text-fg-muted -mt-1">
          Sub-agents the orb can route to. The LLM picks one based on
          its description when it decides delegation is the right call.
        </p>

        {error && (
          <div className="text-xs text-danger break-words">{error}</div>
        )}

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
                    <TypeBadge type={entry.type} />
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
            onCancel={() => setEditing(null)}
            onSaved={onSaved}
          />
        ) : (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setEditing({ kind: 'create' })}
          >
            <Plus className="h-3.5 w-3.5 mr-1" />
            Add delegate
          </Button>
        )}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Editor (create + edit share the form; only the submit verb differs)
// ---------------------------------------------------------------------------

type EditingState =
  | { kind: 'create' }
  | { kind: 'edit'; original: DelegateWithStatus };

function defaultDelegate(): Delegate {
  return {
    name: '',
    type: 'a2a',
    description: '',
    url: '',
    auth: { scheme: 'apiKey', credentialsEnv: '' },
    headers: {},
  };
}

interface EditorProps {
  state: EditingState;
  onCancel: () => void;
  onSaved: (next: DelegateWithStatus[]) => void;
}

function DelegateEditor({ state, onCancel, onSaved }: EditorProps) {
  const isEdit = state.kind === 'edit';
  const [draft, setDraft] = useState<Delegate>(
    () => (isEdit ? state.original : defaultDelegate()),
  );
  const [test, setTest] = useState<DelegateTestResult | 'checking' | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSwitchType = useCallback(
    (next: 'a2a' | 'openai') => {
      // Type switch resets type-specific fields to safe defaults but
      // keeps name + description + url so the user doesn't lose what
      // they've typed.
      setDraft((prev) => {
        const common = {
          name: prev.name,
          description: prev.description,
          url: prev.url,
        };
        if (next === 'a2a') {
          return {
            ...common, type: 'a2a',
            auth: { scheme: 'apiKey', credentialsEnv: '' },
            headers: {},
          };
        }
        return {
          ...common, type: 'openai',
          model: '',
          api_key_env: '',
        };
      });
      setTest(null);
    },
    [],
  );

  const onTest = useCallback(async () => {
    setError(null);
    setTest('checking');
    try {
      const r = await api.delegates.test(draft);
      setTest(r);
    } catch (e) {
      setTest({ ok: false, error: (e as Error).message });
    }
  }, [draft]);

  const onSave = useCallback(async () => {
    setError(null);
    setSaving(true);
    try {
      const r = isEdit
        ? await api.delegates.update(state.original.name, draft)
        : await api.delegates.create(draft);
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

      {/* Type picker */}
      <div className="grid grid-cols-2 gap-2">
        <TypeTile
          active={draft.type === 'a2a'}
          label="A2A agent"
          blurb="JSON-RPC fleet peer (ava, quinn, etc.)"
          onClick={() => onSwitchType('a2a')}
        />
        <TypeTile
          active={draft.type === 'openai'}
          label="OpenAI-compat"
          blurb="Any /v1/chat/completions endpoint"
          onClick={() => onSwitchType('openai')}
        />
      </div>

      <Field
        label="Name"
        hint="Used in delegate_to(target=...). Lowercase, no spaces."
      >
        <input
          value={draft.name}
          onChange={(e) =>
            setDraft({ ...draft, name: e.target.value } as Delegate)
          }
          disabled={isEdit}
          placeholder="ava"
          className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-faint font-mono disabled:opacity-50"
          spellCheck={false}
        />
      </Field>

      <Field
        label="Description"
        hint="The LLM reads this to pick between delegates — be specific."
      >
        <input
          value={draft.description}
          onChange={(e) =>
            setDraft({ ...draft, description: e.target.value } as Delegate)
          }
          placeholder="Chief of staff — sitreps, planning, fleet delegation."
          className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-faint"
          spellCheck={false}
        />
      </Field>

      <Field
        label="URL"
        hint={
          draft.type === 'a2a'
            ? 'JSON-RPC endpoint, typically ending in /a2a. Supports ${VAR:-default}.'
            : 'OpenAI-compat base URL ending in /v1.'
        }
      >
        <input
          value={draft.url}
          onChange={(e) => setDraft({ ...draft, url: e.target.value } as Delegate)}
          placeholder={draft.type === 'a2a' ? 'http://ava:3008/a2a' : 'http://gateway:4000/v1'}
          className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-faint font-mono"
          spellCheck={false}
        />
      </Field>

      {/* Type-specific fields */}
      {draft.type === 'a2a' ? (
        <A2AFields draft={draft} setDraft={setDraft} />
      ) : (
        <OpenAIFields draft={draft} setDraft={setDraft} />
      )}

      {/* Test + save row */}
      <div className="flex items-center gap-2 pt-1">
        <Button
          variant="secondary"
          size="sm"
          onClick={onTest}
          disabled={test === 'checking'}
        >
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
// Type-specific field groups
// ---------------------------------------------------------------------------

function A2AFields({
  draft, setDraft,
}: {
  draft: DelegateA2A;
  setDraft: (d: Delegate) => void;
}) {
  const auth = draft.auth ?? {};
  return (
    <div className="space-y-3">
      <Field
        label="Auth scheme"
        hint="How the delegate expects credentials, if any."
      >
        <select
          value={auth.scheme ?? ''}
          onChange={(e) =>
            setDraft({
              ...draft,
              auth: {
                ...auth,
                scheme: (e.target.value || undefined) as 'apiKey' | 'bearer' | undefined,
              },
            } as Delegate)
          }
          className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body font-mono"
        >
          <option value="">(none — public endpoint)</option>
          <option value="apiKey">apiKey (X-API-Key header)</option>
          <option value="bearer">bearer (Authorization: Bearer)</option>
        </select>
      </Field>
      {auth.scheme && (
        <Field
          label="Credentials env var"
          hint="Name of the env var holding the secret. Set it in .env on the host."
        >
          <input
            value={auth.credentialsEnv ?? ''}
            onChange={(e) =>
              setDraft({
                ...draft,
                auth: { ...auth, credentialsEnv: e.target.value },
              } as Delegate)
            }
            placeholder="AVA_API_KEY"
            className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-faint font-mono"
            spellCheck={false}
          />
        </Field>
      )}
    </div>
  );
}

function OpenAIFields({
  draft, setDraft,
}: {
  draft: DelegateOpenAI;
  setDraft: (d: Delegate) => void;
}) {
  return (
    <div className="space-y-3">
      <Field label="Model" hint="Provider-specific model id.">
        <input
          value={draft.model}
          onChange={(e) => setDraft({ ...draft, model: e.target.value } as Delegate)}
          placeholder="claude-opus-4-6"
          className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-faint font-mono"
          spellCheck={false}
        />
      </Field>
      <Field
        label="API key env var"
        hint="Optional. Leave empty for endpoints that don't need auth."
      >
        <input
          value={draft.api_key_env ?? ''}
          onChange={(e) =>
            setDraft({ ...draft, api_key_env: e.target.value } as Delegate)
          }
          placeholder="LITELLM_MASTER_KEY"
          className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-faint font-mono"
          spellCheck={false}
        />
      </Field>
      <Field
        label="System prompt (optional)"
        hint="Overrides the default 'spoken-aloud, plain-text' framing."
      >
        <textarea
          value={draft.system_prompt ?? ''}
          onChange={(e) =>
            setDraft({ ...draft, system_prompt: e.target.value } as Delegate)
          }
          placeholder="Answer thoroughly but concisely (2-4 sentences). Plain text only."
          rows={2}
          className="w-full rounded-md border border-edge bg-raised/60 px-2.5 py-1.5 text-xs text-fg-body placeholder-fg-faint leading-snug"
          spellCheck={false}
        />
      </Field>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tiny presentation helpers
// ---------------------------------------------------------------------------

function TypeBadge({ type }: { type: 'a2a' | 'openai' }) {
  const label = type === 'a2a' ? 'A2A' : 'OpenAI';
  return (
    <span className="text-helper uppercase tracking-wider text-fg-subtle border border-edge rounded px-1.5 py-0.5">
      {label}
    </span>
  );
}

function TypeTile({
  active, label, blurb, onClick,
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
