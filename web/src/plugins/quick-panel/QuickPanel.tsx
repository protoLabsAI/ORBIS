import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { voiceStore } from '@/voice/state';
import { pushStatusTransient } from '@/plugins/status-pill/store';
import { api, type OrbisConfig } from '@/lib/api';
import { VerbositySelector } from '@/plugins/settings-panel/VerbositySelector';
import { SectionLabel } from '@/components/ui/section-label';
import { Hint } from '@/components/ui/hint';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
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

        <Switch label="Microphone" on={micListening} onToggle={toggleMic} />
        <Switch label="Allow interruptions" on={act.full_duplex} onToggle={() => act.setFullDuplex(!act.full_duplex)} />

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

function Switch({
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
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={label}
        onClick={onToggle}
        className={cn(
          'relative h-5 w-9 shrink-0 rounded-full border transition-colors',
          on ? 'border-brand bg-brand/80' : 'border-edge bg-raised',
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 h-3.5 w-3.5 rounded-full bg-fg transition-transform',
            on ? 'translate-x-4' : 'translate-x-0.5',
          )}
        />
      </button>
    </div>
  );
}
