import type { WakeModel } from '@/lib/api';

export type ActivationStyle = 'push_to_talk' | 'wake_word' | 'open_mic';
export interface ActivationConfig {
  style: ActivationStyle;
  model: string;
  threshold: number;
  listen_window_s: number;
  full_duplex: boolean;
}

export const DEFAULT_ACTIVATION: ActivationConfig = {
  style: 'push_to_talk',
  model: 'hey_jarvis',
  threshold: 0.5,
  listen_window_s: 12,
  full_duplex: false,
};

export function wakeReady(models: WakeModel[], selected: string): boolean {
  const shared = models.filter((m) => m.kind === 'shared');
  return shared.length === 2 && shared.every((m) => m.downloaded)
    && models.some((m) => m.id === selected && m.kind === 'wake' && m.downloaded);
}

/** Commit UI state only after the native command verifies and persists it. */
export async function persistActivation(
  cfg: ActivationConfig,
  write: (command: string, args: Record<string, unknown>) => Promise<unknown>,
): Promise<ActivationConfig> {
  await write('set_activation_config', {
    style: cfg.style,
    model: cfg.model,
    threshold: cfg.threshold,
    listenWindowS: cfg.listen_window_s,
  });
  return cfg;
}
