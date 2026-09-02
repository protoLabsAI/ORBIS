import { voiceIsReady, type VoiceLifecycle } from './lifecycle';

export async function toggleVoiceWhenReady({
  readLifecycle,
  readListening,
  setListening,
}: {
  readLifecycle: () => Promise<VoiceLifecycle | null>;
  readListening: () => Promise<boolean>;
  setListening: (on: boolean) => Promise<void>;
}): Promise<boolean> {
  if (!voiceIsReady(await readLifecycle())) return false;
  const listening = await readListening();
  await setListening(!listening);
  return true;
}

export async function retryVoiceWithRefresh({
  retry,
  readLifecycle,
  applyLifecycle,
}: {
  retry: () => Promise<{ lifecycle: VoiceLifecycle | null }>;
  readLifecycle: () => Promise<VoiceLifecycle | null>;
  applyLifecycle: (lifecycle: VoiceLifecycle) => void;
}): Promise<void> {
  try {
    const result = await retry();
    if (result.lifecycle) applyLifecycle(result.lifecycle);
  } catch (error) {
    // A stale retry CTA can race the backend consuming its one-shot socket.
    // Reconcile the authoritative lifecycle so a 409 becomes Relaunch, not a
    // permanently stale Retry button.
    try {
      const lifecycle = await readLifecycle();
      if (lifecycle) applyLifecycle(lifecycle);
    } catch {
      // Preserve the original retry failure for the caller's status message.
    }
    throw error;
  }
}
