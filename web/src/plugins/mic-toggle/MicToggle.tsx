import { useSyncExternalStore, useCallback } from 'react';
import { Mic, MicOff } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { voiceStore } from '@/voice/state';
import { pushStatusTransient } from '@/sdk';

/**
 * Quick mute/unmute toggle in the right-edge chrome rail (below the
 * reminders bell). Mirrors the orb's double-click push-to-talk: both flip
 * `voiceStore.micListening` and call the `set_mic_listening` Tauri command,
 * so the icon and the orb always agree on whether the mic is hot.
 *
 * Default state is muted (micListening=false) — the slashed MicOff icon —
 * matching the engine, which starts with input gated until the user opts in.
 */
export function MicToggle() {
  const listening = useSyncExternalStore(
    voiceStore.subscribe,
    () => voiceStore.getSnapshot().micListening,
  );

  const toggle = useCallback(() => {
    const next = !voiceStore.getSnapshot().micListening;
    voiceStore.update({ micListening: next });
    pushStatusTransient(next ? 'listening…' : 'muted', 1800);
    invoke('set_mic_listening', { on: next }).catch(() => {
      // Command failed — revert so the icon doesn't lie about engine state.
      voiceStore.update({ micListening: !next });
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
        aria-label={listening ? 'Mute microphone' : 'Unmute microphone'}
        aria-pressed={!listening}
        title={listening ? 'Listening — click to mute' : 'Muted — click to listen'}
        className="relative grid place-items-center h-11 w-11 sm:h-10 sm:w-10 rounded-full bg-transparent text-fg-subtle/60 hover:text-fg-body focus-visible:text-fg-body focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-fg-faint transition-colors"
      >
        {listening ? (
          <Mic className="h-[18px] w-[18px]" strokeWidth={1.5} />
        ) : (
          <MicOff className="h-[18px] w-[18px]" strokeWidth={1.5} />
        )}
      </button>
    </div>
  );
}
