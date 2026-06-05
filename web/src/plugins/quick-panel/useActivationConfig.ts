import { useCallback, useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';

export type ActivationStyle = 'push_to_talk' | 'wake_word' | 'open_mic';

interface ActivationConfig {
  style: ActivationStyle;
  model: string;
  threshold: number;
  listen_window_s: number;
  full_duplex: boolean;
}

const DEFAULTS: ActivationConfig = {
  style: 'push_to_talk',
  model: 'hey_orbis',
  threshold: 0.5,
  listen_window_s: 12,
  full_duplex: false,
};

/**
 * Read/write the Rust-side activation config (`activation.json`) the way
 * WakeWordSettings does, for the Quick tab's compact controls.
 *
 * - `setStyle` rewrites the full config and flags `needsRelaunch` (the detector
 *   only reads style at startup).
 * - `setFullDuplex` applies live (the engine reads it per frame) — no relaunch.
 */
export function useActivationConfig() {
  const [cfg, setCfg] = useState<ActivationConfig>(DEFAULTS);
  const [needsRelaunch, setNeedsRelaunch] = useState(false);

  useEffect(() => {
    let cancelled = false;
    invoke('get_activation_config')
      .then((raw) => {
        if (!cancelled) {
          setCfg({ ...DEFAULTS, ...((raw as Partial<ActivationConfig>) ?? {}) });
        }
      })
      .catch(() => {
        /* fall back to DEFAULTS */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setStyle = useCallback(
    async (style: ActivationStyle) => {
      const next = { ...cfg, style };
      setCfg(next);
      setNeedsRelaunch(true);
      try {
        await invoke('set_activation_config', {
          style: next.style,
          model: next.model,
          threshold: next.threshold,
          listenWindowS: next.listen_window_s,
        });
      } catch {
        /* leave optimistic state; WakeWordSettings owns error surfacing */
      }
    },
    [cfg],
  );

  const setFullDuplex = useCallback(async (on: boolean) => {
    setCfg((c) => ({ ...c, full_duplex: on }));
    try {
      await invoke('set_full_duplex', { on });
    } catch {
      /* applies live; ignore transient failure */
    }
  }, []);

  return { ...cfg, needsRelaunch, setStyle, setFullDuplex };
}
