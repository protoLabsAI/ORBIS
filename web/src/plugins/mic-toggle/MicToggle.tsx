import { useSyncExternalStore, useCallback } from 'react';
import { Mic, MicOff } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { voiceStore } from '@/voice/state';
import { pushStatusTransient } from '@/sdk';

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

  const toggle = useCallback(() => {
    const next = !voiceStore.getSnapshot().micMuted;
    // Muting also closes any open listening turn (the engine does the same), so
    // the UI doesn't show a stale "listening" after unmute.
    voiceStore.update(next ? { micMuted: true, micListening: false } : { micMuted: false });
    pushStatusTransient(next ? 'muted' : 'unmuted', 1800);
    invoke('set_mic_muted', { muted: next }).catch(() => {
      // Command failed — revert so the icon doesn't lie about engine state.
      voiceStore.update({ micMuted: !next });
      pushStatusTransient('mic toggle failed', 2400);
    });
  }, []);

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
      <button
        type="button"
        onClick={toggle}
        aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
        aria-pressed={muted}
        title={muted ? 'Muted — click to unmute' : 'Mic live — click to mute'}
        className="relative grid place-items-center h-11 w-11 sm:h-10 sm:w-10 rounded-full bg-transparent text-fg-subtle/60 hover:text-fg-body focus-visible:text-fg-body focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-fg-faint transition-colors"
      >
        {muted ? (
          <MicOff className="h-[18px] w-[18px]" strokeWidth={1.5} />
        ) : (
          <Mic className="h-[18px] w-[18px]" strokeWidth={1.5} />
        )}
      </button>
    </div>
  );
}
