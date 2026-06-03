/**
 * Subscribe to wake-word model download progress.
 *
 * The backend pushes `wakeword-download` events onto the /api/events SSE
 * bus while a model downloads; the Rust shell's `bridge_sse` re-emits each
 * as a Tauri `orbis-sse` event (the reliable server->client channel in the
 * bundled WKWebView — a streamed fetch body is not). We listen for those and
 * keep a per-model-id progress map the picker renders progress bars from.
 *
 * Payload shape (event === 'wakeword-download', data is JSON):
 *   { id, downloaded, total, pct }   — a progress tick
 *   { id, done: true }               — completed
 *   { id, error: string }            — failed
 */

import { useEffect, useState } from 'react';
import { listen } from '@tauri-apps/api/event';

export interface WakeDownloadState {
  pct: number;
  downloaded: number;
  total: number;
  done: boolean;
  error?: string;
}

export type WakeDownloadMap = Record<string, WakeDownloadState>;

interface SsePayload {
  event: string;
  data: string;
}

export function useWakewordDownloads(): WakeDownloadMap {
  const [progress, setProgress] = useState<WakeDownloadMap>({});

  useEffect(() => {
    let alive = true;
    let unlisten: (() => void) | undefined;

    listen<SsePayload>('orbis-sse', (e) => {
      if (e.payload.event !== 'wakeword-download') return;
      let d: Record<string, unknown>;
      try {
        d = JSON.parse(e.payload.data) as Record<string, unknown>;
      } catch {
        return;
      }
      const id = typeof d.id === 'string' ? d.id : '';
      if (!id) return;
      setProgress((prev) => {
        const cur = prev[id];
        return {
          ...prev,
          [id]: {
            pct: typeof d.pct === 'number' ? d.pct : cur?.pct ?? 0,
            downloaded:
              typeof d.downloaded === 'number'
                ? d.downloaded
                : cur?.downloaded ?? 0,
            total: typeof d.total === 'number' ? d.total : cur?.total ?? 0,
            done: d.done === true || cur?.done || false,
            error: typeof d.error === 'string' ? d.error : undefined,
          },
        };
      });
    }).then((u) => {
      if (alive) unlisten = u;
      else u();
    });

    return () => {
      alive = false;
      unlisten?.();
    };
  }, []);

  return progress;
}
