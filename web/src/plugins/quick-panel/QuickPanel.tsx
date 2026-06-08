import { useCallback, useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { voiceStore } from '@/voice/state';
import { pushStatusTransient } from '@/sdk';
import { api, type OrbisConfig, type StarterOrb } from '@/lib/api';
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
import { useActivationConfig, type ActivationStyle } from './useActivationConfig';

const STYLE_LABELS: Record<ActivationStyle, string> = {
  push_to_talk: 'Push-to-talk',
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
  const micListening = useSyncExternalStore(
    voiceStore.subscribe,
    () => voiceStore.getSnapshot().micListening,
  );

  const toggleMic = useCallback(() => {
    const next = !voiceStore.getSnapshot().micListening;
    voiceStore.update({ micListening: next });
    pushStatusTransient(next ? 'listening…' : 'muted', 1800);
    invoke('set_mic_listening', { on: next }).catch(() => {
      voiceStore.update({ micListening: !next });
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
        <StatusRow label="Microphone" value={micListening ? 'Listening' : 'Muted'} tone={micListening ? 'live' : 'idle'} />
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

        <SwitchRow label="Microphone" on={micListening} onToggle={toggleMic} />
        <SwitchRow label="Allow interruptions" on={act.full_duplex} onToggle={() => act.setFullDuplex(!act.full_duplex)} />

        <OrbSwitcher />

        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm text-fg-body">Activation</span>
            <Select value={act.style} onValueChange={(v) => act.setStyle(v as ActivationStyle)}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="push_to_talk">Push-to-talk</SelectItem>
                <SelectItem value="wake_word">Wake word</SelectItem>
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
 * Free starter-orb switcher — cycles the curated pool (8 by default) via
 * select_starter. Always available, independent of the paid customization
 * editor: it applies the focused starter to the live orb and persists it.
 */
function OrbSwitcher() {
  const [starters, setStarters] = useState<StarterOrb[]>([]);
  const [index, setIndex] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .starterOrbs()
      .then((r) => {
        if (cancelled) return;
        setStarters(r.starters);
        const snap = orbStore.get().getSnapshot();
        const i = r.starters.findIndex(
          (s) => s.variant === snap.variantId && s.palette === snap.palette,
        );
        if (i >= 0) setIndex(i);
      })
      .catch(() => {
        /* no starters configured — the switcher just hides */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const step = (dir: number) => {
    if (starters.length === 0) return;
    const next = (index + dir + starters.length) % starters.length;
    setIndex(next);
    const s = starters[next];
    setVariant(s.variant); // live + broadcast
    applyPreset(s.palette);
    api.selectStarter(s.slug).catch(() => {
      /* persist in background; the live orb already changed */
    });
  };

  if (starters.length === 0) return null;

  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-sm text-fg-body">Orb</span>
      <div className="flex items-center gap-1">
        <ArrowButton label="Previous orb" onClick={() => step(-1)}>
          <ChevronLeft className="h-4 w-4" strokeWidth={1.5} />
        </ArrowButton>
        <span className="min-w-[6.5rem] truncate text-center text-sm text-fg-muted">
          {starters[index]?.name ?? '—'}
        </span>
        <ArrowButton label="Next orb" onClick={() => step(1)}>
          <ChevronRight className="h-4 w-4" strokeWidth={1.5} />
        </ArrowButton>
      </div>
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
