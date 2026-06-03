import { useEffect, useState } from 'react';
import { Check, Download, Loader2, Trash2 } from 'lucide-react';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { Hint } from '@/components/ui/hint';
import { cn } from '@/lib/utils';
import { api, type WakeModel } from '@/lib/api';
import { useWakewordDownloads } from '@/shared/wakeword/useWakewordDownloads';

const fmtSize = (kb: number) =>
  kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`;

/**
 * Wake-word model picker.
 *
 * Lists the openWakeWord catalog (GET /api/wakeword/models), downloads
 * models with live progress (pushed over the orbis-sse bridge — see
 * useWakewordDownloads), and persists the chosen wake word + on/off into
 * config (``wakeword`` key). The two shared models (melspectrogram +
 * embedding) every wake word depends on are pulled automatically the first
 * time a wake word is downloaded.
 *
 * The Rust detector (separate slice) reads the downloaded files + the
 * ``wakeword`` config at launch; changes here apply on the next launch.
 */
export function WakeWordSettings() {
  const [models, setModels] = useState<WakeModel[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [active, setActive] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [needsRelaunch, setNeedsRelaunch] = useState(false);
  const progress = useWakewordDownloads();

  // Reload the catalog + persisted wakeword config. Reused by the action
  // handlers (post-download / -delete) — those are event handlers, not the
  // mount effect, so a direct setState here is fine.
  const refresh = async () => {
    const [cat, cfg] = await Promise.all([
      api.wakeword.models(),
      api.config(),
    ]);
    setModels(cat.models);
    const ww = cfg.config?.wakeword ?? {};
    setEnabled(Boolean(ww.enabled));
    setActive(ww.model ?? '');
  };

  useEffect(() => {
    let cancelled = false;
    // Inline the initial load (not via refresh()) so the setState lands in a
    // .then callback — matches STTSettings and keeps the effect lint clean.
    Promise.all([api.wakeword.models(), api.config()])
      .then(([cat, cfg]) => {
        if (cancelled) return;
        setModels(cat.models);
        const ww = cfg.config?.wakeword ?? {};
        setEnabled(Boolean(ww.enabled));
        setActive(ww.model ?? '');
      })
      .catch((e) => {
        if (!cancelled) setError(String((e as Error).message ?? e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const wakeModels = models.filter((m) => m.kind === 'wake');
  const sharedDeps = models.filter((m) => m.kind === 'shared');
  const sharedReady =
    sharedDeps.length > 0 && sharedDeps.every((d) => d.downloaded);
  const anyWakeReady = wakeModels.some((m) => m.downloaded);
  const canEnable = sharedReady && anyWakeReady && Boolean(active);

  // Persist the wakeword config patch, merging onto the current state.
  const saveConfig = async (patch: { enabled?: boolean; model?: string }) => {
    try {
      await api.putConfig({
        wakeword: { enabled, model: active || undefined, ...patch },
      });
      setNeedsRelaunch(true);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  const download = async (id: string) => {
    setError(null);
    setBusyId(id);
    try {
      // Every wake word needs the two shared models — pull any missing first.
      for (const dep of sharedDeps.filter((d) => !d.downloaded)) {
        await api.wakeword.download(dep.id);
      }
      await api.wakeword.download(id);
      await refresh();
      // First wake word installed becomes the active selection.
      const m = models.find((x) => x.id === id);
      if (m?.kind === 'wake' && !active) {
        setActive(id);
        await saveConfig({ model: id });
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
      await api.wakeword.remove(id);
      if (active === id) {
        setActive('');
        await saveConfig({ model: '' });
      }
      await refresh();
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  const selectActive = async (id: string) => {
    setActive(id);
    await saveConfig({ model: id });
  };

  const toggleEnabled = async () => {
    const next = !enabled;
    setEnabled(next);
    await saveConfig({ enabled: next });
  };

  if (loading) {
    return (
      <Panel title="Wake word">
        <div className="text-xs text-fg-subtle">Loading…</div>
      </Panel>
    );
  }

  return (
    <Panel title="Wake word">
      <div className="space-y-3">
        <Hint className="-mt-1">
          Start a turn hands-free by speaking a wake word — no click. Runs
          entirely on-device.
        </Hint>

        {/* On/off — a wake word must be installed and selected first. */}
        <button
          type="button"
          onClick={toggleEnabled}
          disabled={!canEnable}
          className="flex items-center gap-2.5 disabled:opacity-40"
          aria-pressed={enabled}
        >
          <span
            className={cn(
              'relative h-5 w-9 rounded-full transition-colors',
              enabled ? 'bg-brand/80' : 'bg-edge',
            )}
          >
            <span
              className={cn(
                'absolute top-0.5 size-4 rounded-full bg-fg transition-all',
                enabled ? 'left-[1.125rem]' : 'left-0.5',
              )}
            />
          </span>
          <span className="text-sm text-fg-body">
            {enabled ? 'Listening for wake word' : 'Wake word off'}
          </span>
        </button>
        {!canEnable && (
          <Hint className="text-fg-faint">
            Download and select a wake word below to enable hands-free
            listening.
          </Hint>
        )}

        {/* Wake-word catalog */}
        <div className="space-y-1.5">
          {wakeModels.map((m) => {
            const p = progress[m.id];
            const downloading =
              busyId === m.id && (!p || (!p.done && !p.error));
            const isActive = active === m.id;
            const selectable = m.downloaded && !isActive;
            return (
              <div
                key={m.id}
                className={cn(
                  'rounded-md border p-2.5 transition-colors',
                  isActive
                    ? 'border-brand/60 bg-brand/5'
                    : 'border-edge bg-raised/40',
                  selectable && 'cursor-pointer hover:bg-raised/70',
                )}
                onClick={selectable ? () => selectActive(m.id) : undefined}
              >
                <div className="flex items-start gap-2.5">
                  {/* Radio dot — filled when active */}
                  <span
                    className={cn(
                      'mt-0.5 size-3.5 shrink-0 rounded-full border',
                      isActive
                        ? 'border-brand bg-brand'
                        : m.downloaded
                          ? 'border-fg-muted'
                          : 'border-edge',
                    )}
                  >
                    {isActive && (
                      <Check className="size-3 text-base" strokeWidth={3} />
                    )}
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

                    {/* Actions / progress */}
                    <div className="mt-2 flex items-center gap-2">
                      {m.downloaded ? (
                        <>
                          <span className="inline-flex items-center gap-1 text-helper text-success">
                            <Check className="size-3" /> Installed
                          </span>
                          {isActive && (
                            <span className="text-helper text-brand">
                              · Active
                            </span>
                          )}
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
                            {p && (
                              <span className="tabular-nums">
                                {Math.round(p.pct)}%
                              </span>
                            )}
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
                          disabled={busyId !== null}
                        >
                          <Download /> Download
                        </Button>
                      )}
                    </div>
                    {p?.error && (
                      <p className="mt-1 text-helper text-danger">{p.error}</p>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* Shared-dependency status footnote */}
        {sharedDeps.length > 0 && (
          <Hint className="text-fg-faint">
            {sharedReady ? '✓ ' : ''}Shared models (
            {sharedDeps.map((d) => d.name).join(' + ')}) ·{' '}
            {sharedReady
              ? 'installed'
              : 'downloaded automatically with your first wake word'}
          </Hint>
        )}

        {needsRelaunch && (
          <Hint className="text-brand/70">
            Takes effect when ORBIS next launches.
          </Hint>
        )}
        {error && <p className="text-xs text-danger">{error}</p>}
      </div>
    </Panel>
  );
}
