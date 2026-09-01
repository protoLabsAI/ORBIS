import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { appLogDir } from '@tauri-apps/api/path';
import { BootGate } from '@protolabsai/ui/splash';
import { Button } from '@/components/ui/button';
import { api } from '@/lib/api';
import { pushStatusTransient } from '@/shared/statusBus';

/**
 * Boot loading gate. The bundled UI paints instantly, but the Python
 * sidecar loads real models on the way up — Whisper/Parakeet STT, Kokoro
 * TTS, the LLM warmup — which on a cold first launch takes a minute or
 * two (downloads + GIL-heavy loads that also stall the API). Letting the
 * user into the wizard during that window means the first save hangs.
 *
 * So we gate the whole UI behind this screen until the sidecar reports
 * `ready`. The stage text reflects ACTUAL backend progress: the sidecar
 * prints `ORBIS_BOOT {stage,detail}` markers as each component loads, the
 * Rust shell forwards them as `orbis-boot` events (and caches the latest
 * for `boot_status`, in case a marker landed before we subscribed).
 *
 * The shell is the design system's BootGate (mark + spinner + title +
 * detail + action — the same surface protoAgent boots through); this
 * component keeps all the ORBIS-specific stage/progress/stall logic and
 * slots the progress bar + escape hatch in.
 */

interface BootStage {
  stage: string;
  detail: string;
}

function parseBoot(raw: string): BootStage | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as BootStage;
    return typeof v?.stage === 'string' ? v : null;
  } catch {
    return null;
  }
}

// Fraction of the boot the gate shows as complete at each stage. The STT
// step is the long pole (first-run model download), so it claims the
// biggest slice — the bar advances meaningfully as each stage lands.
const STAGE_PROGRESS: Record<string, number> = {
  stt: 0.3,
  tts: 0.6,
  llm: 0.85,
  ready: 1,
};

// The cold start is legitimately slow AND opaque: the Python sidecar pays ~80s
// of heavy ML imports (torch / MLX / pipecat) on every launch before the first
// progress marker — and right after an update, pyapp re-extracts its env on top
// of that — so the gate sits on "Starting ORBIS…" for a minute+ with nothing
// moving. That's normal here, not a hang. (Measured ~80s spawn→ORBIS_READY.)
//
// So: a gentle reassurance line after a short wait, and the actual escape hatch
// (View logs / Continue anyway) only after a wait *well past* a normal cold
// start + model warmup — so a genuinely wedged boot isn't an infinite spinner,
// without crying wolf on every launch.
const REASSURE_AFTER_S = 15;
const SLOW_AFTER_S = 180;
const HARD_AFTER_S = 300;
const HUB_HEALTH_RETRY_MS = 500;
// Backend confirmation is capped at 2s + 1s retry + 2s. Keep three seconds of
// margin for startup scheduling and transient local /healthz failures.
const HUB_HEALTH_DEADLINE_MS = 8000;
const HUB_HEALTH_FETCH_TIMEOUT_MS = 1000;
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

  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let cancelled = false;
    let hubHealthStarted = false;
    const healthTimers = new Map<number, () => void>();

    const clearHealthTimer = (id: number) => {
      window.clearTimeout(id);
      healthTimers.delete(id);
    };

    const waitForHealth = (delayMs: number) => new Promise<void>((resolve) => {
      const finish = () => {
        healthTimers.delete(id);
        resolve();
      };
      const id = window.setTimeout(finish, delayMs);
      healthTimers.set(id, finish);
    });

    const fetchHealth = async () => {
      let timeoutId = 0;
      const timeout = new Promise<never>((_, reject) => {
        const fail = () => {
          healthTimers.delete(timeoutId);
          reject(new Error('health request timed out'));
        };
        timeoutId = window.setTimeout(fail, HUB_HEALTH_FETCH_TIMEOUT_MS);
        healthTimers.set(timeoutId, fail);
      });
      try {
        return await Promise.race([api.health(), timeout]);
      } finally {
        clearHealthTimer(timeoutId);
      }
    };

    const warnIfHubUnavailable = async () => {
      // The background probe normally lands before the ready marker. If the
      // snapshot is still unknown, retry briefly without holding the boot gate.
      const deadline = performance.now() + HUB_HEALTH_DEADLINE_MS;
      while (!cancelled && performance.now() < deadline) {
        try {
          const health = await fetchHealth();
          if (cancelled) return;
          const hub = health.delegates.find((delegate) => delegate.name === 'hub');
          if (!hub || hub.ok === true) return;
          if (hub.ok === false && hub.consecutive_failures >= 2) {
            if (cancelled) return;
            pushStatusTransient(HUB_UNAVAILABLE_WARNING, 8000);
            return;
          }
        } catch {
          // Backend diagnostics being unavailable is not evidence that the hub
          // is down. Retry inside the bounded startup window; the normal boot /
          // error surfaces own a sustained sidecar failure.
        }
        const remaining = deadline - performance.now();
        if (remaining <= 0) return;
        await waitForHealth(Math.min(HUB_HEALTH_RETRY_MS, remaining));
        if (cancelled) return;
      }
    };

    const apply = (raw: string) => {
      const s = parseBoot(raw);
      if (!s || cancelled) return;
      if (s.detail) {
        setDetail(s.detail);
        // "Loading Parakeet speech model…" → real load; "…loads on first use" → deferred.
        if (/loading\b.*\bmodel/i.test(s.detail)) setLoadingModels(true);
      }
      const p = STAGE_PROGRESS[s.stage];
      if (p !== undefined) setProgress((prev) => Math.max(prev, p));
      if (s.stage === 'ready') {
        setReady(true);
        if (!hubHealthStarted) {
          hubHealthStarted = true;
          void warnIfHubUnavailable();
        }
      }
    };

    // Catch markers emitted before this component subscribed.
    invoke<string>('boot_status').then(apply).catch(() => {
      // Command unavailable (non-Tauri dev) — don't block the UI.
      if (!cancelled) setReady(true);
    });

    listen<string>('orbis-boot', (e) => apply(e.payload))
      .then((fn) => {
        if (cancelled) fn();
        else unlisten = fn;
      })
      .catch(() => {});

    return () => {
      cancelled = true;
      healthTimers.forEach((finish, id) => {
        clearHealthTimer(id);
        finish();
      });
      healthTimers.clear();
      if (unlisten) unlisten();
    };
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
