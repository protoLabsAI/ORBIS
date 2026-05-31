import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

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

export function BootStatus() {
  const [detail, setDetail] = useState<string>('Starting ORBIS…');
  const [progress, setProgress] = useState(0.05);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let cancelled = false;

    const apply = (raw: string) => {
      const s = parseBoot(raw);
      if (!s || cancelled) return;
      if (s.detail) setDetail(s.detail);
      const p = STAGE_PROGRESS[s.stage];
      if (p !== undefined) setProgress((prev) => Math.max(prev, p));
      if (s.stage === 'ready') setReady(true);
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
      if (unlisten) unlisten();
    };
  }, []);

  if (ready) return null;

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-5 bg-[#0a0a0a] text-zinc-300">
      <div className="h-10 w-10 animate-spin rounded-full border-2 border-zinc-700 border-t-amber-400" />
      <div className="text-base">{detail}</div>
      <div className="h-1 w-56 overflow-hidden rounded-full bg-zinc-800">
        <div
          className="h-full rounded-full bg-amber-400 transition-[width] duration-700 ease-out"
          style={{ width: `${Math.round(progress * 100)}%` }}
        />
      </div>
      <div className="max-w-xs text-center text-sm text-zinc-500">
        First launch loads local speech + language models — this can take a
        minute or two. Later launches are quick.
      </div>
    </div>
  );
}
