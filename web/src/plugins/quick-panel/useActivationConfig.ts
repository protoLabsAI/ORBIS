import { useCallback, useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { api, type WakeModel } from '@/lib/api';
import {
  DEFAULT_ACTIVATION, persistActivation, wakeReady,
  type ActivationConfig, type ActivationStyle,
} from '@/shared/wakeword/activation';
export type { ActivationStyle } from '@/shared/wakeword/activation';

/** Native activation is saved for next launch; duplex applies live. */
export function useActivationConfig() {
  const [cfg, setCfg] = useState<ActivationConfig>(DEFAULT_ACTIVATION);
  const [models, setModels] = useState<WakeModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsRelaunch, setNeedsRelaunch] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // A catalog failure must not prevent reading Tap to talk / Open mic.
    invoke<Partial<ActivationConfig>>('get_activation_config')
      .then((raw) => { if (!cancelled) { setCfg({ ...DEFAULT_ACTIVATION, ...raw }); setLoaded(true); } })
      .catch(() => { if (!cancelled) setError('Could not load activation settings.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    api.wakeword.models()
      .then((cat) => { if (!cancelled) setModels(cat.models); })
      .catch(() => { /* Wake remains unavailable until the catalog loads. */ });
    return () => { cancelled = true; };
  }, []);

  const setStyle = useCallback(async (style: ActivationStyle) => {
    setError(null);
    try {
      const next = await persistActivation({ ...cfg, style }, invoke);
      setCfg(next);
      setNeedsRelaunch(true);
    } catch (e) {
      setError(String(e));
    }
  }, [cfg]);

  const setFullDuplex = useCallback(async (on: boolean) => {
    try {
      await invoke('set_full_duplex', { on });
      setCfg((c) => ({ ...c, full_duplex: on }));
    } catch (e) {
      setError(String(e));
    }
  }, []);

  return { ...cfg, loading, loaded, error, canWake: wakeReady(models, cfg.model), needsRelaunch, setStyle, setFullDuplex };
}
