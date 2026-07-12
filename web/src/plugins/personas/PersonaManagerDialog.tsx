/**
 * Persona manager dialog (epic #611 P3, #609) — create / edit /
 * duplicate / delete persona files without touching the filesystem.
 *
 * The persona FILE stays the source of truth: saves go through
 * `PUT /api/personas/{slug}` which writes `<app_data>/personas/<slug>.md`
 * (hand-editable, shareable). Bundled starters are read-only — the edit
 * path is Duplicate, which creates a user file; a user file with a
 * bundled slug *shadows* the original and deleting it un-shadows.
 *
 * Meta keys this form doesn't surface (extends, tools, llm.url, …) are
 * preserved verbatim on save — the draft spreads the original meta and
 * overrides only the edited fields, so a hand-authored file survives a
 * dialog round-trip.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { pushStatusTransient } from '@/sdk';
import { api, type PersonaEntry, type StarterOrb } from '@/lib/api';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Hint } from '@/components/ui/hint';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { applyPreset, setVariant } from '@/plugins/orb/broadcast';
import { variantRegistry } from '@/plugins/orb/variants/registry';
import { isRuntimeOrb } from '@/plugins/orb/definitions/runtime';

const INHERIT = '__inherit__';
const VERBOSITIES = ['silent', 'brief', 'narrated', 'chatty'] as const;

type Draft = {
  slug: string;
  /** Unsaved create/duplicate — the file doesn't exist server-side yet. */
  isNew: boolean;
  name: string;
  description: string;
  voice: string;
  orb: string; // INHERIT or a starter slug / variant id / raw ref
  temperature: string; // keep as text; parse on save
  verbosity: string; // INHERIT or one of VERBOSITIES
  model: string; // optional llm.model override
  prompt: string;
  /** Original frontmatter, preserved for keys the form doesn't edit. */
  meta: Record<string, unknown>;
};

function slugify(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 64) || 'persona'
  );
}

function draftFrom(p: PersonaEntry, isNew = false): Draft {
  const meta = (p.meta ?? {}) as Record<string, unknown>;
  const voice = (meta.voice ?? {}) as Record<string, unknown>;
  const llm = (meta.llm ?? {}) as Record<string, unknown>;
  return {
    slug: p.slug,
    isNew,
    name: p.name,
    description: p.description ?? '',
    voice: typeof voice.voice === 'string' ? voice.voice : '',
    orb: typeof meta.orb === 'string' ? meta.orb : INHERIT,
    temperature: meta.temperature != null ? String(meta.temperature) : '',
    verbosity:
      typeof meta.filler_verbosity === 'string' ? meta.filler_verbosity : INHERIT,
    model: typeof llm.model === 'string' ? llm.model : '',
    prompt: p.prompt ?? '',
    meta,
  };
}

/** Serialize the draft back to the PUT body, preserving unedited meta. */
function bodyFrom(d: Draft): Record<string, unknown> {
  const body: Record<string, unknown> = { ...d.meta };
  body.name = d.name.trim() || d.slug;
  if (d.description.trim()) body.description = d.description.trim();
  else delete body.description;

  const voice = { ...((d.meta.voice as Record<string, unknown>) ?? {}) };
  if (d.voice.trim()) voice.voice = d.voice.trim();
  else delete voice.voice;
  if (Object.keys(voice).length) body.voice = voice;
  else delete body.voice;

  if (d.orb !== INHERIT) body.orb = d.orb;
  else delete body.orb;

  const t = parseFloat(d.temperature);
  if (d.temperature.trim() && Number.isFinite(t)) body.temperature = t;
  else delete body.temperature;

  if (d.verbosity !== INHERIT) body.filler_verbosity = d.verbosity;
  else delete body.filler_verbosity;

  const llm = { ...((d.meta.llm as Record<string, unknown>) ?? {}) };
  if (d.model.trim()) llm.model = d.model.trim();
  else delete llm.model;
  if (Object.keys(llm).length) body.llm = llm;
  else delete body.llm;

  body.prompt = d.prompt;
  return body;
}

export function PersonaManagerDialog({
  open,
  onOpenChange,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Fired after any mutation (save / delete / set-active) so the
   * Quick-tab picker can refetch. */
  onChanged: () => void;
}) {
  const [personas, setPersonas] = useState<PersonaEntry[]>([]);
  const [active, setActive] = useState('default');
  const [starters, setStarters] = useState<StarterOrb[]>([]);
  const [voices, setVoices] = useState<Array<{ id: string; label: string }>>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const refetch = useCallback(async (selectSlug?: string) => {
    const r = await api.personas();
    setPersonas(r.personas);
    setActive(r.active);
    const pick =
      r.personas.find((p) => p.slug === (selectSlug ?? r.active)) ??
      r.personas[0];
    if (pick) setDraft(draftFrom(pick));
  }, []);

  useEffect(() => {
    if (!open) return;
    setConfirmDelete(false);
    refetch().catch(() => pushStatusTransient('personas unavailable', 2400));
    api.starterOrbs().then((r) => setStarters(r.starters)).catch(() => {});
    api
      .ttsVoices('kokoro')
      .then((r) => setVoices(r.voices ?? []))
      .catch(() => {});
  }, [open, refetch]);

  const selected = personas.find((p) => p.slug === draft?.slug);
  const readOnly = !draft?.isNew && selected != null && !selected.editable;
  const isDefault = draft?.slug === 'default' && !draft?.isNew;
  const importedOrbs = useMemo(
    () => variantRegistry.all().filter((v) => isRuntimeOrb(v.id)),
    // re-read while open; registry is stable within a dialog session
    [open], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const select = (p: PersonaEntry) => {
    setConfirmDelete(false);
    setDraft(draftFrom(p));
  };

  const startNew = (from?: PersonaEntry) => {
    setConfirmDelete(false);
    const base: PersonaEntry = from ?? {
      slug: '',
      name: '',
      description: '',
      source: 'user',
      editable: true,
      meta: {},
      prompt: '',
    };
    const name = from ? `${from.name} Copy` : 'New persona';
    let slug = slugify(name);
    const taken = new Set(personas.map((p) => p.slug));
    for (let n = 2; taken.has(slug); n += 1) slug = `${slugify(name)}-${n}`;
    setDraft({ ...draftFrom({ ...base, name }, true), slug, isNew: true });
  };

  const save = async () => {
    if (!draft || busy) return;
    setBusy(true);
    // A new/duplicated draft takes its slug from the final name — that's
    // what lets "Duplicate → keep the name" shadow the bundled original.
    // Never silently claim an EXISTING user file's slug though; keep the
    // pre-uniquified one in that case.
    let slug = draft.slug;
    if (draft.isNew) {
      const derived = slugify(draft.name);
      const takenByUser = personas.some((p) => p.slug === derived && p.editable);
      if (!takenByUser) slug = derived;
    }
    try {
      const r = await api.putPersona(slug, bodyFrom(draft));
      pushStatusTransient(
        r.shadows_bundled ? `saved (shadows bundled ${slug})` : 'persona saved',
        2400,
      );
      await refetch(slug);
      onChanged();
    } catch {
      pushStatusTransient('save failed', 2400);
    } finally {
      setBusy(false);
    }
  };

  const activate = async () => {
    if (!draft || busy) return;
    setBusy(true);
    try {
      const r = await api.setActivePersona(draft.slug);
      if (r.viz?.variant) setVariant(r.viz.variant);
      if (r.viz?.palette) applyPreset(r.viz.palette);
      pushStatusTransient(`persona: ${r.name}`, 2400);
      setActive(r.active);
      onChanged();
    } catch {
      pushStatusTransient('switch failed', 2400);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!draft || busy) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setBusy(true);
    try {
      await api.deletePersona(draft.slug);
      pushStatusTransient('persona deleted', 2400);
      await refetch();
      onChanged();
    } catch {
      pushStatusTransient('delete failed', 2400);
    } finally {
      setBusy(false);
      setConfirmDelete(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Personas</DialogTitle>
          <DialogDescription>
            Who the orb is — prompt, voice, model, and orb identity in one
            file. Saved personas live in your app data as markdown.
          </DialogDescription>
        </DialogHeader>

        <div className="flex gap-4 min-h-[22rem]">
          {/* Catalog */}
          <div className="w-44 shrink-0 space-y-1 overflow-y-auto">
            {personas.map((p) => (
              <button
                key={p.slug}
                type="button"
                onClick={() => select(p)}
                className={cn(
                  'w-full rounded-md px-2.5 py-1.5 text-left text-sm transition-colors',
                  draft?.slug === p.slug && !draft?.isNew
                    ? 'bg-raised text-fg-body'
                    : 'text-fg-muted hover:bg-raised/60 hover:text-fg-body',
                )}
              >
                <span className="flex items-center gap-1.5">
                  {p.slug === active && (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-success" />
                  )}
                  <span className="truncate">{p.name}</span>
                </span>
                <span className="block text-micro uppercase tracking-wider text-fg-subtle">
                  {p.source}
                </span>
              </button>
            ))}
            <button
              type="button"
              onClick={() => startNew()}
              className="w-full rounded-md px-2.5 py-1.5 text-left text-sm text-fg-subtle transition-colors hover:bg-raised/60 hover:text-fg-body"
            >
              + New persona
            </button>
          </div>

          {/* Editor */}
          {draft && (
            <div className="min-w-0 flex-1 space-y-3 overflow-y-auto pr-1">
              {isDefault ? (
                <Hint className="text-fg-subtle">
                  The default persona is configured in Settings (Brain / Voice
                  tabs) and orbis.yaml — it isn't a persona file. Pick or
                  create one to edit here.
                </Hint>
              ) : (
                <>
                  {readOnly && (
                    <Hint className="text-fg-subtle">
                      Bundled starter — read-only. Duplicate it to make it
                      yours; saving under the same name shadows the original.
                    </Hint>
                  )}
                  <Row label="Name">
                    <Input
                      value={draft.name}
                      disabled={readOnly}
                      onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                    />
                  </Row>
                  <Row label="Description">
                    <Input
                      value={draft.description}
                      disabled={readOnly}
                      onChange={(e) =>
                        setDraft({ ...draft, description: e.target.value })
                      }
                    />
                  </Row>
                  <Row label="Voice">
                    <>
                      <Input
                        value={draft.voice}
                        disabled={readOnly}
                        list="persona-voice-options"
                        placeholder="inherit"
                        onChange={(e) => setDraft({ ...draft, voice: e.target.value })}
                      />
                      <datalist id="persona-voice-options">
                        {voices.map((v) => (
                          <option key={v.id} value={v.id}>
                            {v.label}
                          </option>
                        ))}
                      </datalist>
                    </>
                  </Row>
                  <Row label="Orb">
                    <Select
                      value={draft.orb}
                      disabled={readOnly}
                      onValueChange={(v) => setDraft({ ...draft, orb: v })}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={INHERIT}>— inherit —</SelectItem>
                        {starters.map((s) => (
                          <SelectItem key={s.slug} value={s.slug}>
                            {s.name}
                          </SelectItem>
                        ))}
                        {importedOrbs.map((v) => (
                          <SelectItem key={v.id} value={v.id}>
                            {v.name} (imported)
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Row>
                  <div className="grid grid-cols-2 gap-3">
                    <Row label="Temperature">
                      <Input
                        value={draft.temperature}
                        disabled={readOnly}
                        placeholder="inherit"
                        inputMode="decimal"
                        onChange={(e) =>
                          setDraft({ ...draft, temperature: e.target.value })
                        }
                      />
                    </Row>
                    <Row label="Verbosity">
                      <Select
                        value={draft.verbosity}
                        disabled={readOnly}
                        onValueChange={(v) => setDraft({ ...draft, verbosity: v })}
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={INHERIT}>— inherit —</SelectItem>
                          {VERBOSITIES.map((v) => (
                            <SelectItem key={v} value={v}>
                              {v}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </Row>
                  </div>
                  <Row label="Model override">
                    <Input
                      value={draft.model}
                      disabled={readOnly}
                      placeholder="inherit active model"
                      onChange={(e) => setDraft({ ...draft, model: e.target.value })}
                    />
                  </Row>
                  <div className="space-y-1">
                    <span className="text-helper text-fg-muted">
                      Prompt <span className="text-fg-subtle">(spoken-voice style — empty inherits the default)</span>
                    </span>
                    <textarea
                      value={draft.prompt}
                      disabled={readOnly}
                      rows={8}
                      spellCheck={false}
                      onChange={(e) => setDraft({ ...draft, prompt: e.target.value })}
                      className="w-full rounded-md border border-edge bg-raised/60 px-2.5 py-1.5 text-xs text-fg-body placeholder-fg-muted leading-snug disabled:opacity-60"
                    />
                  </div>
                </>
              )}

              <div className="flex items-center justify-between gap-2 pt-1">
                <div className="flex items-center gap-2">
                  {!isDefault && !readOnly && (
                    <Button size="sm" disabled={busy} onClick={save}>
                      {draft.isNew ? 'Create' : 'Save'}
                    </Button>
                  )}
                  {readOnly && (
                    <Button size="sm" disabled={busy} onClick={() => startNew(selected)}>
                      Duplicate
                    </Button>
                  )}
                  {!draft.isNew && draft.slug !== active && (
                    <Button size="sm" variant="outline" disabled={busy} onClick={activate}>
                      Set active
                    </Button>
                  )}
                </div>
                {!isDefault && !readOnly && !draft.isNew && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={remove}
                    className={cn(
                      'text-helper transition-colors disabled:opacity-50',
                      confirmDelete
                        ? 'text-danger'
                        : 'text-fg-subtle hover:text-danger',
                    )}
                  >
                    {confirmDelete ? 'Really delete?' : 'Delete'}
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <span className="text-helper text-fg-muted">{label}</span>
      {children}
    </div>
  );
}
