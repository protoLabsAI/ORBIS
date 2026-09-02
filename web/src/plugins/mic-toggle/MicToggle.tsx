import { useSyncExternalStore, useCallback } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { voiceStore } from '@/voice/state';
import { pushStatusTransient } from '@/sdk';
import { voiceIsReady, voiceLifecycleText } from '@/voice/lifecycle';
import { VoiceMicButton } from '@/voice/components';

/**
 * Hard mic-mute toggle in the right-edge chrome rail (below the reminders
 * bell). This is the top-level override: muting cuts ALL mic input in the Rust
 * engine — no STT, no wake word, no barge-in, truly silent — independent of the
 * push-to-talk / wake-word activation layer. Unmuting restores it.
 *
 * Activation (starting a listening turn) is a separate concern: double-click
 * the orb or say the wake word. Both are blocked while muted ("mute is king").
 * Boots live (unmuted); the mute is session-only.
 */
export function MicToggle() {
  const muted = useSyncExternalStore(
    voiceStore.subscribe,
    () => voiceStore.getSnapshot().micMuted,
  );
  const lifecycle = useSyncExternalStore(
    voiceStore.subscribe,
    () => voiceStore.getSnapshot().voiceLifecycle,
  );
  const ready = voiceIsReady(lifecycle);

  const toggle = useCallback(() => {
    const next = !voiceStore.getSnapshot().micMuted;
    if (!ready) {
      pushStatusTransient(voiceLifecycleText(lifecycle), 2400);
      return;
    }
    // Muting also closes any open listening turn (the engine does the same), so
    // the UI doesn't show a stale "listening" after unmute.
    voiceStore.update(next ? { micMuted: true, micListening: false } : { micMuted: false });
    pushStatusTransient(next ? 'muted' : 'unmuted', 1800);
    invoke('set_mic_muted', { muted: next }).catch(() => {
      // Command failed — revert so the icon doesn't lie about engine state.
      voiceStore.update({ micMuted: !next });
      pushStatusTransient('mic toggle failed', 2400);
    });
  }, [lifecycle, ready]);

  return (
    <div
      className="fixed z-20"
      style={{
        // Stack below the reminders bell, which sits below the settings gear:
        // gear top: 0.75rem (~2.75rem tall), bell at +3rem, mic at +6rem.
        top: 'calc(0.75rem + 6rem + env(safe-area-inset-top, 0px))',
        right: 'calc(0.75rem + env(safe-area-inset-right, 0px))',
      }}
    >
      <VoiceMicButton
        ready={ready}
        muted={muted}
        unavailableText={voiceLifecycleText(lifecycle)}
        onClick={toggle}
      />
    </div>
  );
}
