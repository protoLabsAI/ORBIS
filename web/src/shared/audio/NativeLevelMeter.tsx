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
        // `get_audio_level` returns RAW mic RMS from the Rust engine. The
        // compensating MIC_GAIN lives downstream in voice/local_transport.py,
        // not here, and the RMS is computed pre-gain — so on the M1 internal
        // mic (no hardware AGC) normal speech is only ~0.013 RMS. The old
        // linear `rms * 6` mapped that to 0.08, lighting barely one bar, which
        // read as "no mic pickup" in settings. A dBFS curve spans the wide
        // dynamic range between a quiet ungained internal mic and a hot
        // external/AGC mic (~0.1–0.3 RMS) so normal speech lands mid-meter on
        // both, instead of a linear gain that pegs hot mics or hides quiet ones.
        const dbfs = rms > 0 ? 20 * Math.log10(rms) : -Infinity;
        // -50 dBFS (near-silent room floor) → empty; -6 dBFS (hot) → full.
        setLevel(Math.max(0, Math.min(1, (dbfs + 50) / 44)));
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
