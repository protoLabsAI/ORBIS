import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Check, Download, Loader2, Trash2 } from 'lucide-react';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { Hint } from '@/components/ui/hint';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import { api, type WakeModel } from '@/lib/api';
import { useWakewordDownloads } from '@/shared/wakeword/useWakewordDownloads';
import { WAKE_WORD_ENABLED } from '@/shared/wakeword/enabled';

const fmtSize = (kb: number) =>
  kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`;

import { DEFAULT_ACTIVATION as DEFAULTS, persistActivation, persistDownloadedWakeSelection, wakeReady, type ActivationConfig, type ActivationStyle as Style } from '@/shared/wakeword/activation';

const loadActivation = async (): Promise<ActivationConfig> => {
  const raw = await invoke<Partial<ActivationConfig>>('get_activation_config');
  return { ...DEFAULTS, ...raw };
};
const loadCatalog = () => api.wakeword.models();

const STYLES: { id: Style; label: string; hint: string }[] = [
  { id: 'push_to_talk', label: 'Tap to talk', hint: 'Double-click the orb to talk (default).' },
  { id: 'wake_word', label: 'Wake word', hint: 'Hands-free — say the phrase to start a turn.' },
  { id: 'open_mic', label: 'Open mic', hint: 'Always listening; never auto-closes.' },
];

/**
 * Activation settings (engagement-modes Axis 1) + the wake-word model picker.
 *
 * The activation config (style / model / threshold / listen window) is the
 * source of truth the **Rust** detector reads at launch — persisted via the
 * `set_activation_config` Tauri command into `<app_data>/activation.json`, NOT
 * the Python config (the detector is Rust-side). Model files are still managed
 * through the catalog (`/api/wakeword`, downloaded on-device). Changes apply on
 * the next launch (the detector spawns once at startup).
 */
export function WakeWordSettings() {
  const [models, setModels] = useState<WakeModel[]>([]);
  const [cfg, setCfg] = useState<ActivationConfig>(DEFAULTS);
  const [loading, setLoading] = useState(true);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [needsRelaunch, setNeedsRelaunch] = useState(false);
  const progress = useWakewordDownloads();

  const refresh = async () => {
    const [cat, act] = await Promise.all([loadCatalog(), loadActivation()]);
    setModels(cat.models);
    setCfg(act);
    return act;
  };

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([loadCatalog(), loadActivation()])
      .then(([cat, act]) => {
        if (cancelled) return;
        if (act.status === 'fulfilled') { setCfg(act.value); setConfigLoaded(true); }
        if (cat.status === 'fulfilled') setModels(cat.value.models);
        const failure = cat.status === 'rejected' ? cat.reason : act.status === 'rejected' ? act.reason : null;
        setError(failure ? String(failure) : null);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reload]);

  const wakeModels = models.filter((m) => m.kind === 'wake');
  const sharedDeps = models.filter((m) => m.kind === 'shared');
  const sharedReady = sharedDeps.length > 0 && sharedDeps.every((d) => d.downloaded);


  // Persist the full activation config (the Rust detector reads this).
  const save = async (patch: Partial<ActivationConfig>) => {
    const next = { ...cfg, ...patch };
    setError(null);
    try {
      setCfg(await persistActivation(next, invoke));
      setNeedsRelaunch(true);
      return true;
    } catch (e) {
      setError(String((e as Error).message ?? e));
      return false;
    }
  };

  // Full-duplex / barge-in applies LIVE (the engine reads it per frame) — its
  // own Tauri command, so no relaunch banner.
  const saveFullDuplex = async (on: boolean) => {
    try {
      await invoke('set_full_duplex', { on });
      setCfg((c) => ({ ...c, full_duplex: on }));
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  const download = async (id: string) => {
    setError(null);
    setBusyId(id);
    try {
      for (const dep of sharedDeps.filter((d) => !d.downloaded)) {
        await api.wakeword.download(dep.id);
      }
      await api.wakeword.download(id);
      const m = models.find((x) => x.id === id);
      if (m?.kind === 'wake') {
        setCfg(await persistDownloadedWakeSelection(id, refresh, invoke));
        setNeedsRelaunch(true);
      } else {
        await refresh();
      }
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (id: string) => {
    setError(null);
    try {
      if (cfg.model === id && !await save({ model: '', style: 'push_to_talk' })) return;
      await api.wakeword.remove(id);
      await refresh();
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  if (loading) {
    return (
      <Panel title="Activation">
        <div className="text-xs text-fg-subtle">Loading…</div>
      </Panel>
    );
  }

  const wakeSelected = cfg.style === 'wake_word';
  const canWake = wakeReady(models, cfg.model);
  const phrase = wakeModels.find((m) => m.id === cfg.model)?.name ?? 'wake word';

  return (
    <Panel title="Activation">
      <div className="space-y-3">
        <Hint className="-mt-1">How the mic goes hot. Wake word runs entirely on-device.</Hint>

        {/* Wake mode is opt-in and requires the selected model and shared files. */}
        <div className="space-y-1.5">
          {STYLES.filter((s) => WAKE_WORD_ENABLED || s.id !== 'wake_word').map((s) => {
            const isActive = cfg.style === s.id;
            const disabled = !configLoaded || (s.id === 'wake_word' && !canWake);
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => !disabled && save({ style: s.id })}
                disabled={disabled}
                aria-pressed={isActive}
                className={cn(
                  'flex w-full items-start gap-2.5 rounded-md border p-2.5 text-left transition-colors disabled:opacity-40',
                  isActive ? 'border-brand/60 bg-brand/5' : 'border-edge bg-raised/40 hover:bg-raised/70',
                )}
              >
                <span
                  className={cn(
                    'mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded-full border',
                    isActive ? 'border-brand bg-brand' : 'border-fg-muted',
                  )}
                >
                  {isActive && <Check className="size-3 text-base" strokeWidth={3} />}
                </span>
                <span className="min-w-0">
                  <span className="text-sm text-fg-body">{s.label}</span>
                  <Hint className="mt-0.5">{s.hint}</Hint>
                </span>
              </button>
            );
          })}
        </div>
        {WAKE_WORD_ENABLED && !canWake && (
          <Hint className="text-fg-faint">
            Open “Wake words &amp; tuning” below to download and select a wake word to enable
            hands-free listening.
          </Hint>
        )}

        {/* Listen window — a behavior knob, not tuning esoterica, so it lives
            up here with the style choice instead of inside the disclosure
            (it was buried there and nobody found it). Wake-word style only:
            push-to-talk closes per turn, open mic never closes. */}
        {wakeSelected && (
          <label className="block space-y-1 rounded-md border border-edge bg-raised/40 p-2.5">
            <span className="flex items-center justify-between text-helper text-fg-muted">
              <span>Listen window</span>
              <span className="tabular-nums text-fg-subtle">{cfg.listen_window_s}s</span>
            </span>
            <input
              type="range"
              min={4}
              max={30}
              step={1}
              value={cfg.listen_window_s}
              onChange={(e) => save({ listen_window_s: Number(e.target.value) })}
              className="w-full accent-brand"
            />
            <Hint className="text-fg-faint">
              After the conversation goes quiet for this long, the mic closes back
              to armed (“{phrase}” reopens
              it). She holds the window while thinking, working on a task, or speaking.
            </Hint>
          </label>
        )}

        {/* Barge-in (full-duplex) — applies live, all activation styles */}
        <div className="flex items-start justify-between gap-3 rounded-md border border-edge bg-raised/40 p-2.5">
          <span className="min-w-0">
            <span className="text-sm text-fg-body">Allow interruptions</span>
            <Hint className="mt-0.5">
              Keep the mic open while she speaks so you can cut in mid-sentence. Use with
              headphones — on speakers she may hear herself and interrupt her own reply.
            </Hint>
          </span>
          <Switch
            className="mt-0.5"
            checked={cfg.full_duplex}
            onCheckedChange={saveFullDuplex}
            disabled={!configLoaded}
            aria-label="Allow interruptions (full-duplex)"
          />
        </div>

        {/* Phrase downloads and advanced tuning. */}
        {WAKE_WORD_ENABLED && (
        <details className="group rounded-md border border-edge bg-raised/30">
          <summary className="flex cursor-pointer select-none items-center justify-between px-3 py-2 text-helper uppercase tracking-wider text-fg-muted hover:text-fg-body">
            <span>Wake words &amp; tuning</span>
            <span className="text-fg-faint transition-transform group-open:rotate-90">›</span>
          </summary>
          <div className="space-y-3 px-3 pb-3 pt-1">
            <Hint>Hey Orbis is experimental. Try Hey Jarvis while we validate real microphone accuracy. Double-click the orb remains available if a phrase misses.</Hint>

        {/* Wake-word tuning — only relevant in wake-word style */}
        {wakeSelected && (
          <div className="space-y-3 rounded-md border border-edge bg-raised/30 p-3">
            <label className="block space-y-1">
              <span className="flex items-center justify-between text-helper text-fg-muted">
                <span>Sensitivity</span>
                <span className="tabular-nums text-fg-subtle">
                  threshold {cfg.threshold.toFixed(2)}
                </span>
              </span>
              <input
                type="range"
                min={0.1}
                max={0.9}
                step={0.05}
                value={cfg.threshold}
                onChange={(e) => save({ threshold: Number(e.target.value) })}
                className="w-full accent-brand"
              />
              <Hint className="text-fg-faint">Lower = fires more easily (more false triggers).</Hint>
            </label>
          </div>
        )}

        {/* Wake-word catalog */}
        <div className="space-y-1.5">
          {wakeModels.map((m) => {
            const p = progress[m.id];
            const downloading = busyId === m.id;
            const isActive = cfg.model === m.id;
            const selectable = configLoaded && sharedReady && m.downloaded && !isActive;
            return (
              <div
                key={m.id}
                className={cn(
                  'rounded-md border p-2.5 transition-colors',
                  isActive ? 'border-brand/60 bg-brand/5' : 'border-edge bg-raised/40',
                  selectable && 'cursor-pointer hover:bg-raised/70',
                )}
                onClick={selectable ? () => save({ model: m.id }) : undefined}
              >
                <div className="flex items-start gap-2.5">
                  <span
                    className={cn(
                      'mt-0.5 size-3.5 shrink-0 rounded-full border',
                      isActive ? 'border-brand bg-brand' : m.downloaded ? 'border-fg-muted' : 'border-edge',
                    )}
                  >
                    {isActive && <Check className="size-3 text-base" strokeWidth={3} />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-fg-body">{m.name}</span>
                      {m.recommended && (
                        <span className="rounded bg-brand/15 px-1.5 py-px text-micro uppercase tracking-wide text-brand">
                          Recommended
                        </span>
                      )}
                      <span className="ml-auto shrink-0 text-helper tabular-nums text-fg-subtle">
                        {fmtSize(m.size_kb)}
                      </span>
                    </div>
                    <Hint className="mt-0.5 line-clamp-2">{m.description}</Hint>
                    <button type="button" className="mt-1 text-helper text-fg-subtle underline underline-offset-2 hover:text-fg-body"
                      onClick={(e) => { e.stopPropagation(); invoke('open_url', { url: m.source_url }).catch(() => {}); }}>
                      Source · {m.license}
                    </button>
                    <div className="mt-2 flex items-center gap-2">
                      {m.downloaded ? (
                        <>
                          <span className="inline-flex items-center gap-1 text-helper text-success">
                            <Check className="size-3" /> Installed
                          </span>
                          {isActive && <span className="text-helper text-brand">· Active</span>}
                          <Button
                            size="xs"
                            variant="ghost"
                            className="ml-auto text-fg-subtle hover:text-danger"
                            onClick={(e) => {
                              e.stopPropagation();
                              remove(m.id);
                            }}
                            aria-label={`Remove ${m.name}`}
                          >
                            <Trash2 />
                          </Button>
                        </>
                      ) : downloading ? (
                        <div className="flex-1 space-y-1">
                          <div className="flex items-center justify-between text-helper text-fg-subtle">
                            <span className="inline-flex items-center gap-1">
                              <Loader2 className="size-3 animate-spin" />
                              {p ? 'Downloading' : 'Preparing…'}
                            </span>
                            {p && <span className="tabular-nums">{Math.round(p.pct)}%</span>}
                          </div>
                          <div className="h-1 overflow-hidden rounded-full bg-edge">
                            <div
                              className="h-full bg-brand/80 transition-[width] duration-200"
                              style={{ width: `${p ? p.pct : 8}%` }}
                            />
                          </div>
                        </div>
                      ) : (
                        <Button
                          size="xs"
                          variant="outline"
                          onClick={(e) => {
                            e.stopPropagation();
                            download(m.id);
                          }}
                          disabled={busyId !== null || !configLoaded}
                        >
                          <Download /> Download
                        </Button>
                      )}
                    </div>
                    {p?.error && <p className="mt-1 text-helper text-danger">{p.error}</p>}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <button
          type="button"
          onClick={() =>
            invoke('open_url', {
              url: 'https://orbis.protolabs.studio/docs/how-to/train-a-wake-word',
            }).catch(() => {})
          }
          className="text-left text-helper text-brand/80 underline-offset-2 hover:text-brand hover:underline"
        >
          Train your own wake word →
        </button>

        {sharedDeps.length > 0 && (
          <Hint className="text-fg-faint">
            {sharedReady ? '✓ ' : ''}Shared models ({sharedDeps.map((d) => d.name).join(' + ')}) ·{' '}
            {sharedReady ? 'installed' : 'downloaded automatically with your first wake word'}
          </Hint>
        )}
          </div>
        </details>
        )}

        {needsRelaunch && (
          <Hint className="text-brand/70">Takes effect when ORBIS next launches. Removing the active phrase returns the next launch to Tap to talk.</Hint>
        )}
        {error && <div role="alert" className="space-y-2">
          <p className="text-xs text-danger">{error}</p>
          <Button variant="outline" size="xs" onClick={() => setReload((n) => n + 1)}>Reload activation settings</Button>
        </div>}
      </div>
    </Panel>
  );
}
