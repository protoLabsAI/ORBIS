import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';

/**
 * Live microphone RMS meter backed by the native audio engine.
 * Polls `get_audio_level` (Tauri IPC) every 80 ms. Only renders
 * meaningfully when the native-audio feature is active and the engine
 * has started.
 */
export function NativeLevelMeter({ deviceName }: { deviceName: string }) {
  const [level, setLevel] = useState(0);

  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const rms = await invoke<number>('get_audio_level');
        // Boost RMS so typical speech (0.02–0.1) fills the meter visibly.
        setLevel(Math.min(1, rms * 6));
      } catch {
        // Engine not started yet — stay at zero.
      }
    }, 80);
    return () => clearInterval(id);
  }, [deviceName]);

  const bars = 20;
  return (
    <div className="space-y-2">
      <div className="flex gap-0.5 h-6 items-end">
        {Array.from({ length: bars }).map((_, i) => {
          const threshold = (i + 1) / bars;
          const active = level >= threshold;
          return (
            <div
              key={i}
              className={`flex-1 rounded-sm transition-all duration-75 ${
                active
                  ? i / bars < 0.6
                    ? 'bg-success'
                    : i / bars < 0.85
                      ? 'bg-yellow-400'
                      : 'bg-red-500'
                  : 'bg-edge'
              }`}
              style={{ height: `${40 + (i / bars) * 60}%` }}
            />
          );
        })}
      </div>
      <p className="text-xs text-fg-muted text-center">
        Speak to test — level meter reflects live input
      </p>
    </div>
  );
}
