import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { voiceStore } from '@/voice/state';
import { pushStatusTransient } from '@/sdk';
import { api, type OrbisConfig, type PersonaEntry, type StarterOrb } from '@/lib/api';
import { VerbositySelector } from '@/plugins/settings-panel/VerbositySelector';
import { SectionLabel } from '@/components/ui/section-label';
import { Hint } from '@/components/ui/hint';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { orbStore } from '@/plugins/orb/store';
import { applyPreset, setVariant } from '@/plugins/orb/broadcast';
import { variantRegistry } from '@/plugins/orb/variants/registry';
import {
  importOrbisFile,
  isRuntimeOrb,
  persistSelection,
  removeRuntimeOrb,
} from '@/plugins/orb/definitions/runtime';
import { useActivationConfig, type ActivationStyle } from './useActivationConfig';
import { WAKE_WORD_ENABLED } from '@/shared/wakeword/enabled';
import { PersonaManagerDialog } from '@/plugins/personas/PersonaManagerDialog';

const STYLE_LABELS: Record<ActivationStyle, string> = {
  push_to_talk: 'Tap to talk',
  wake_word: 'Wake word',
  open_mic: 'Open mic',
};

/**
 * Quick tab — the drawer's landing surface: at-a-glance state + the handful of
 * controls you reach for daily, pulled up from the deeper tabs. Everything here
 * is live (mic, interruptions, verbosity) except the activation style, which
 * the Rust detector only reads at launch (shown with a relaunch hint).
 */
export function QuickPanel() {
  const micMuted = useSyncExternalStore(
    voiceStore.subscribe,
    () => voiceStore.getSnapshot().micMuted,
  );

  // The "Microphone" switch is the hard mute (on = mic live, off = muted) —
  // same top-level override as the chrome-rail mic button. Activation
  // (double-click / wake word) is separate and blocked while muted.
  const toggleMute = useCallback(() => {
    const next = !voiceStore.getSnapshot().micMuted;
    voiceStore.update(next ? { micMuted: true, micListening: false } : { micMuted: false });
    pushStatusTransient(next ? 'muted' : 'unmuted', 1800);
    invoke('set_mic_muted', { muted: next }).catch(() => {
      voiceStore.update({ micMuted: !next });
      pushStatusTransient('mic toggle failed', 2400);
    });
  }, []);

  const [config, setConfig] = useState<OrbisConfig | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .config()
      .then((r) => {
        if (!cancelled) {
          setConfig(r.config);
          setConnected(true);
        }
      })
      .catch(() => {
        if (!cancelled) setConnected(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const act = useActivationConfig();

  const model = config?.llm?.model;
  const voice = config?.voice?.voice;
  const needsModel = connected === true && !model;

  return (
    <div className="space-y-5">
      {/* At-a-glance state */}
      <div className="rounded-xl border border-edge bg-raised/40 p-4 space-y-2.5">
        <StatusRow label="Microphone" value={micMuted ? 'Muted' : 'Live'} tone={micMuted ? 'idle' : 'live'} />
        <StatusRow
          label="Connection"
          value={connected === null ? '…' : connected ? 'Connected' : 'Offline'}
          tone={connected === null ? 'idle' : connected ? 'live' : 'off'}
        />
        <StatusRow label="Activation" value={STYLE_LABELS[act.style]} />
        <StatusRow label="Model" value={model || 'Not set'} />
        <StatusRow label="Voice" value={voice || '—'} />
      </div>

      {needsModel && (
        <Hint className="text-brand">Pick a model in the Brain tab to start talking.</Hint>
      )}

      {/* The handful of daily controls */}
      <div className="space-y-3.5">
        <SectionLabel>Quick controls</SectionLabel>

        <SwitchRow label="Microphone" on={!micMuted} onToggle={toggleMute} />
        <SwitchRow label="Allow interruptions" on={act.full_duplex} onToggle={() => act.setFullDuplex(!act.full_duplex)} />

        <PersonaSwitcher />

        <OrbSwitcher />

        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm text-fg-body">Activation</span>
            <Select value={act.style} onValueChange={(v) => act.setStyle(v as ActivationStyle)}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="push_to_talk">Tap to talk</SelectItem>
                {/* Wake word hidden until the retrained model ships — see
                    @/shared/wakeword/enabled. */}
                {WAKE_WORD_ENABLED && <SelectItem value="wake_word">Wake word</SelectItem>}
                <SelectItem value="open_mic">Open mic</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {act.needsRelaunch && (
            <Hint className="text-fg-subtle">Activation change applies on next launch.</Hint>
          )}
        </div>

        <VerbositySelector />
      </div>
    </div>
  );
}

function StatusRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: 'live' | 'idle' | 'off';
}) {
  const dot =
    tone === 'live' ? 'bg-success' : tone === 'off' ? 'bg-danger' : 'bg-fg-faint';
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-helper text-fg-muted">{label}</span>
      <span className="flex items-center gap-2 min-w-0 text-sm text-fg-body">
        {tone && <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', dot)} />}
        <span className="truncate">{value}</span>
      </span>
    </div>
  );
}

function SwitchRow({
  label,
  on,
  onToggle,
}: {
  label: string;
  on: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-fg-body">{label}</span>
      <Switch checked={on} onCheckedChange={onToggle} aria-label={label} />
    </div>
  );
}

/**
 * Persona switcher (epic #611 P2) — who the orb *is*: prompt, voice,
 * model override, orb identity in one pick. The backend hot-swaps the
 * live session (prompt/tools next turn, LLM + voice + filler now); the
 * orb visual applies from the response viz (and the persona-switched
 * SSE event covers every other window). Hidden until a persona file
 * exists beyond the built-in default.
 */
function PersonaSwitcher() {
  const [personas, setPersonas] = useState<PersonaEntry[]>([]);
  const [active, setActive] = useState('default');
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);

  const refetch = useCallback(() => {
    api
      .personas()
      .then((r) => {
        setPersonas(r.personas);
        setActive(r.active);
      })
      .catch(() => {
        /* endpoint unreachable — the row just doesn't appear */
      });
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  const pick = async (slug: string) => {
    if (busy || slug === active) return;
    setBusy(true);
    const prev = active;
    setActive(slug);
    try {
      const r = await api.setActivePersona(slug);
      if (r.viz?.variant) setVariant(r.viz.variant);
      if (r.viz?.palette) applyPreset(r.viz.palette);
      setNote(r.notes?.[0] ?? null);
      pushStatusTransient(`persona: ${r.name}`, 2400);
    } catch {
      setActive(prev);
      pushStatusTransient('persona switch failed', 2400);
    } finally {
      setBusy(false);
    }
  };

  if (personas.length === 0) return null;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm text-fg-body">Persona</span>
        <Select value={active} onValueChange={pick} disabled={busy}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {personas.map((p) => (
              <SelectItem key={p.slug} value={p.slug}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex items-center justify-end">
        <button
          type="button"
          onClick={() => setManageOpen(true)}
          className="text-helper text-fg-subtle transition-colors hover:text-fg-body"
        >
          Manage…
        </button>
      </div>
      {note && <Hint className="text-fg-subtle">{note}</Hint>}
      <PersonaManagerDialog
        open={manageOpen}
        onOpenChange={setManageOpen}
        onChanged={refetch}
      />
    </div>
  );
}

type OrbEntry =
  | { kind: 'starter'; name: string; starter: StarterOrb }
  | { kind: 'imported'; name: string; variantId: string };

/**
 * Orb switcher — cycles the curated starter pool plus any imported
 * `.orbis` orbs (the only selection surface for them while the full
 * Orb editor tab is off for beta). Starters persist via select_starter;
 * imported picks persist through the orb store's local storage.
 * The row beneath imports a `.orbis` file and removes the active
 * imported orb.
 */
function OrbSwitcher() {
  const [starters, setStarters] = useState<StarterOrb[]>([]);
  const variants = useSyncExternalStore(
    variantRegistry.subscribe,
    variantRegistry.all,
    variantRegistry.all,
  );
  const activeVariantId = useSyncExternalStore(
    orbStore.subscribe,
    () => orbStore.getSnapshot().variantId,
  );

  const entries = useMemo<OrbEntry[]>(
    () => [
      ...starters.map((s): OrbEntry => ({ kind: 'starter', name: s.name, starter: s })),
      ...variants
        .filter((v) => isRuntimeOrb(v.id))
        .map((v): OrbEntry => ({ kind: 'imported', name: v.name, variantId: v.id })),
    ],
    [starters, variants],
  );
  const [index, setIndex] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .starterOrbs()
      .then((r) => {
        if (!cancelled) setStarters(r.starters);
      })
      .catch(() => {
        /* no starters configured — starters just don't appear */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the focused entry in sync with whatever is actually live
  // (boot restore, an import that auto-activated, a removal fallback).
  useEffect(() => {
    const snap = orbStore.get().getSnapshot();
    const i = entries.findIndex((e) =>
      e.kind === 'starter'
        ? e.starter.variant === snap.variantId && e.starter.palette === snap.palette
        : e.variantId === snap.variantId,
    );
    if (i >= 0) setIndex(i);
  }, [entries, activeVariantId]);

  const step = (dir: number) => {
    if (entries.length === 0) return;
    const next = (index + dir + entries.length) % entries.length;
    setIndex(next);
    const e = entries[next];
    if (e.kind === 'starter') {
      setVariant(e.starter.variant); // live + broadcast
      applyPreset(e.starter.palette);
      api.selectStarter(e.starter.slug).catch(() => {
        /* persist in background; the live orb already changed */
      });
    } else {
      setVariant(e.variantId); // loads the definition's default palette
      // Server config is the boot source of truth (fresh origin per
      // launch) — persist like select_starter does for starters.
      persistSelection(e.variantId);
    }
  };

  if (entries.length === 0) return null;
  const current = entries[index];

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm text-fg-body">Orb</span>
        <div className="flex items-center gap-1">
          <ArrowButton label="Previous orb" onClick={() => step(-1)}>
            <ChevronLeft className="h-4 w-4" strokeWidth={1.5} />
          </ArrowButton>
          <span className="min-w-[6.5rem] truncate text-center text-sm text-fg-muted">
            {current?.name ?? '—'}
            {current?.kind === 'imported' && (
              <span className="ml-1.5 text-micro uppercase tracking-wider text-fg-subtle">
                imported
              </span>
            )}
          </span>
          <ArrowButton label="Next orb" onClick={() => step(1)}>
            <ChevronRight className="h-4 w-4" strokeWidth={1.5} />
          </ArrowButton>
        </div>
      </div>
      <ImportOrbRow activeVariantId={activeVariantId} />
    </div>
  );
}

/** Quiet utility row under the orb switcher: import a `.orbis` file
 * (authored at orbis.protolabs.studio/editor) and remove the active
 * imported orb. Invalid files are rejected server-side — the error
 * surfaces through the status pill. */
function ImportOrbRow({ activeVariantId }: { activeVariantId: string }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const onFile = async (file: File | undefined) => {
    if (!file || busy) return;
    setBusy(true);
    try {
      const res = await importOrbisFile(file);
      if (res.ok) {
        pushStatusTransient(`imported orb "${res.id}"`, 2400);
      } else {
        pushStatusTransient(`import failed: ${res.errors[0] ?? 'invalid file'}`, 4000);
        console.warn('[orb] .orbis import rejected:', res.errors);
      }
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const onRemove = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const ok = await removeRuntimeOrb(activeVariantId);
      pushStatusTransient(ok ? 'orb removed' : 'remove failed', 2400);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center justify-end gap-3">
      <input
        ref={fileRef}
        type="file"
        accept=".orbis,application/json"
        className="hidden"
        onChange={(e) => onFile(e.target.files?.[0])}
      />
      <button
        type="button"
        disabled={busy}
        onClick={() => fileRef.current?.click()}
        className="text-helper text-fg-subtle transition-colors hover:text-fg-body disabled:opacity-50"
      >
        Import .orbis
      </button>
      {isRuntimeOrb(activeVariantId) && (
        <button
          type="button"
          disabled={busy}
          onClick={onRemove}
          className="text-helper text-fg-subtle transition-colors hover:text-danger disabled:opacity-50"
        >
          Remove
        </button>
      )}
    </div>
  );
}

function ArrowButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="grid h-7 w-7 place-items-center rounded-md text-fg-subtle transition-colors hover:bg-raised hover:text-fg-body"
    >
      {children}
    </button>
  );
}
