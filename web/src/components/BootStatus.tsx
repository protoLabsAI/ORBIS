import { useEffect, useRef, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { appLogDir } from '@tauri-apps/api/path';
import { BootGate } from '@protolabsai/ui/splash';
import { Button } from '@/components/ui/button';
import { pushStatusTransient } from '@/shared/statusBus';
import { api } from '@/lib/api';
import { voiceStore } from '@/voice/state';
import {
  createBootSignals,
  startBootStatusRuntime,
  type BootStage,
  type SsePayload,
} from './bootStatusRuntime';

/**
 * Boot loading gate. The bundled UI paints instantly, but the Python
 * sidecar has a real import/startup phase before its API and settings surfaces
 * are safe. Letting the user into the wizard during that window means the
 * first save can race startup and hang.
 *
 * So we gate the whole UI only until the sidecar reports `app-ready`.
 * Voice model preparation continues behind the app and is represented by the
 * separate `voice-lifecycle` contract in StatusPill and voice controls. The
 * stage text here reflects ACTUAL backend progress: the sidecar
 * prints `ORBIS_BOOT {stage,detail}` markers as each component loads, the
 * Rust shell forwards them as `orbis-boot` events (and caches the latest
 * for `boot_status`, in case a marker landed before we subscribed).
 *
 * The shell is the design system's BootGate (mark + spinner + title +
 * detail + action — the same surface protoAgent boots through); this
 * component keeps all the ORBIS-specific stage/progress/stall logic and
 * slots the progress bar + escape hatch in.
 */

// Fraction of startup shown at each legacy/new marker. `ready` remains as a
// compatibility marker; `app-ready` is the application gate.
const STAGE_PROGRESS: Record<string, number> = {
  stt: 0.3,
  tts: 0.6,
  llm: 0.85,
  ready: 1,
  'app-ready': 1,
};

// The cold start is legitimately slow AND opaque: the Python sidecar pays ~80s
// of heavy ML imports (torch / MLX / pipecat) on every launch before the first
// progress marker — and right after an update, pyapp re-extracts its env on top
// of that — so the gate sits on "Starting ORBIS…" for a minute+ with nothing
// moving. That's normal here, not a hang. (Measured ~80s spawn→ORBIS_READY.)
//
// So: a gentle reassurance line after a short wait, and the actual escape hatch
// (View logs / Continue anyway) only after a wait *well past* normal app
// startup — so a genuinely wedged boot isn't an infinite spinner,
// without crying wolf on every launch.
const REASSURE_AFTER_S = 15;
const SLOW_AFTER_S = 180;
const HARD_AFTER_S = 300;
const HUB_UNAVAILABLE_WARNING =
  'protoAgent unavailable — start the local hub to restore delegation';

export function BootStatus() {
  const [detail, setDetail] = useState<string>('Starting ORBIS…');
  const [progress, setProgress] = useState(0.05);
  const [ready, setReady] = useState(false);
  // Only show the "first launch loads local models" caveat when an on-device
  // model is ACTUALLY loading. If the user opted for cloud/BYO — or hasn't
  // chosen yet (new user) — the sidecar defers the load and boot is quick, so
  // claiming otherwise contradicts the wizard's own "download these models?"
  // step. We infer it from the backend's stage detail ("Loading X model…").
  const [loadingModels, setLoadingModels] = useState(false);
  // Seconds since mount, ticked until ready — drives the slow/stalled escape
  // hatch below so a wedged boot isn't an infinite spinner.
  const [elapsed, setElapsed] = useState(0);
  // React StrictMode replays effects without resetting refs. Keep the
  // user-visible warning dedupe outside the effect-local runtime session.
  const hubWarningShown = useRef(false);

  useEffect(() => {
    const applyBootStage = (s: BootStage) => {
      if (s.detail) {
        setDetail(s.detail);
        // "Loading Parakeet speech model…" → real load; "…loads on first use" → deferred.
        if (/loading\b.*\bmodel/i.test(s.detail)) setLoadingModels(true);
      }
      const p = STAGE_PROGRESS[s.stage];
      if (p !== undefined) setProgress((prev) => Math.max(prev, p));
    };
    const signals = createBootSignals({
      onBootStage: applyBootStage,
      onApplicationReady: () => setReady(true),
      onHubUnavailable: () => {
        if (hubWarningShown.current) return;
        hubWarningShown.current = true;
        pushStatusTransient(HUB_UNAVAILABLE_WARNING, 8000);
      },
      onVoiceLifecycle: (voiceLifecycle) => {
        voiceStore.update({ voiceLifecycle });
      },
    });

    return startBootStatusRuntime(
      {
        listenBoot: (handler) => listen<string>(
          'orbis-boot', (event) => handler(event.payload),
        ),
        listenSse: (handler) => listen<SsePayload>(
          'orbis-sse', (event) => handler(event.payload),
        ),
        bootSnapshot: () => invoke<string>('boot_status'),
        hubHealthSnapshot: () => invoke<SsePayload | null>('delegate_health_status'),
        voiceLifecycleSnapshot: async () => {
          const lifecycle = (await api.health()).voice?.lifecycle;
          return lifecycle
            ? { event: 'voice-lifecycle', data: JSON.stringify(lifecycle) }
            : null;
        },
      },
      signals,
      // Commands/listeners are unavailable in plain browser development.
      () => setReady(true),
    );
  }, []);

  // Tick a seconds counter until ready.
  useEffect(() => {
    if (ready) return;
    const id = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, [ready]);

  if (ready) return null;

  // True while still in the opaque pre-marker phase (no boot stage reported yet).
  const noMarkerYet = progress <= 0.05;
  // Gentle "this is normal" line during a slow cold start, before any marker and
  // when there's no model-download caveat already reassuring the user.
  const reassure = noMarkerYet && !loadingModels && elapsed >= REASSURE_AFTER_S;
  // Escape hatch only after a long wait — later still when a model is actively
  // downloading (expected-slow; the caveat already explains that).
  const stalled =
    elapsed >= HARD_AFTER_S || (elapsed >= SLOW_AFTER_S && !loadingModels);

  const onViewLogs = () => {
    // Reveal the unified log dir in Finder (open_url → shell.open). Best-effort.
    appLogDir()
      .then((dir) => invoke('open_url', { url: dir }))
      .catch(() => {});
  };

  const caveat = stalled
    ? loadingModels
      ? 'Still downloading — a first-run model pull can run several minutes on a slow connection.'
      : 'Still starting — this is taking longer than usual.'
    : loadingModels
      ? 'First launch loads local speech models — this can take a minute or two. Later launches are quick.'
      : reassure
        ? 'First launch takes a moment — warming up the runtime. Hang tight.'
        : undefined;

  // No logo here on purpose: the Splash bumper just showed the brand mark
  // full-screen — repeating it on the gate reads as a stutter. The gate is
  // spinner + stage text + progress bar only.
  return (
    <BootGate
      title={detail}
      detail={caveat}
      action={
        <div className="flex flex-col items-center gap-3">
          <div className="h-1 w-56 overflow-hidden rounded-full bg-edge">
            {noMarkerYet ? (
              // No real progress yet (the ~80s import phase). Show an
              // indeterminate sweep so it reads as "working", not a frozen
              // 5% bar.
              <div className="orbis-boot-indeterminate h-full w-1/3 rounded-full bg-brand" />
            ) : (
              <div
                className="h-full rounded-full bg-brand transition-[width] duration-700 ease-out"
                style={{ width: `${Math.round(progress * 100)}%` }}
              />
            )}
          </div>
          <style>{`
            @keyframes orbis-boot-indeterminate {
              0% { transform: translateX(-120%); }
              100% { transform: translateX(320%); }
            }
            .orbis-boot-indeterminate {
              animation: orbis-boot-indeterminate 1.3s ease-in-out infinite;
            }
          `}</style>
          {stalled && (
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" onClick={onViewLogs}>
                View logs
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setReady(true)}>
                Continue anyway
              </Button>
            </div>
          )}
        </div>
      }
    />
  );
}
