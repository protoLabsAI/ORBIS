import { useEffect, useRef, useSyncExternalStore } from 'react';
import { useVoiceStateSelector, useEffectiveVoiceLifecycle } from '@/voice/hooks';
import { statusBus } from '@/shared/statusBus';
import { voiceIsReady, voiceLifecycleText } from '@/voice/lifecycle';
import { useThinkingText } from '@/voice/thinkingEscalation';
import { useToolActivityText } from '@/voice/toolActivity';
import { delegationTasks, liveCount } from '@/voice/delegationTasks';

/**
 * Bottom-of-screen status hint.
 *
 * Pre-2026-04-28 this also showed connect/connecting/error states for
 * the WebRTC transport. The web/PWA path was dropped (DECISIONS.md
 * amendment of that date), so the native pipeline is always live and
 * the only cases we show are: a persistent hint, an externally-pushed
 * transient (from pushStatusTransient), or nothing.
 */
export function StatusPill() {
  const connected = useVoiceStateSelector((s) => s.connected);
  const activation = useVoiceStateSelector((s) => s.activation);
  const wakePhrase = useVoiceStateSelector((s) => s.wakePhrase);
  const voiceState = useVoiceStateSelector((s) => s.state);
  const micListening = useVoiceStateSelector((s) => s.micListening);
  const micMuted = useVoiceStateSelector((s) => s.micMuted);
  const voiceLifecycle = useEffectiveVoiceLifecycle();
  const activeToolCall = useVoiceStateSelector((s) => s.activeToolCall);
  const delegationProgress = useVoiceStateSelector((s) => s.delegationProgress);
  const delegationOutcome = useVoiceStateSelector((s) => s.delegationOutcome);
  const lastOutcomeRef = useRef<typeof delegationOutcome>(null);
  const externalTransient = useSyncExternalStore(
    statusBus.subscribe,
    statusBus.getSnapshot,
  );
  // Elapsed-time escalation so a slow LLM doesn't sit on a static "thinking…".
  const thinkingLabel = useThinkingText(voiceState === 'thinking');
  const liveDelegations = liveCount(
    useSyncExternalStore(delegationTasks.subscribe, delegationTasks.getSnapshot),
  );
  const toolText = useToolActivityText(activeToolCall, liveDelegations);
  const delegationText = delegationProgress ?? toolText;

  // Auto-expire the externally-pushed transient once its TTL hits.
  useEffect(() => {
    if (!externalTransient || externalTransient.expiresAt === 0) return;
    const remaining = externalTransient.expiresAt - Date.now();
    if (remaining <= 0) {
      statusBus.clear();
      return;
    }
    const id = window.setTimeout(() => statusBus.clear(), remaining);
    return () => window.clearTimeout(id);
  }, [externalTransient]);

  useEffect(() => {
    if (delegationOutcome === 'error' && lastOutcomeRef.current !== 'error') {
      statusBus.push('delegation failed', 4000);
    }
    lastOutcomeRef.current = delegationOutcome;
  }, [delegationOutcome]);

  // Bot pipeline state (driven by the sidecar's `bot-state` SSE) takes
  // precedence over the idle mic hint — while ORBIS is talking or thinking,
  // say so instead of "listening…". An active delegation/tool call is more
  // specific still, so it wins above this.
  const text = (!voiceIsReady(voiceLifecycle) ? voiceLifecycleText(voiceLifecycle) : null)
    ?? externalTransient?.text
    ?? delegationText
    ?? (micMuted
      ? 'muted'
      : !connected
      ? 'voice status reconnecting…'
      : voiceState === 'speaking'
        ? 'speaking…'
        : voiceState === 'thinking'
          ? thinkingLabel
          : micListening
            ? 'listening…'
            : activation === 'starting'
              ? 'loading wake word…'
              : activation === 'failed'
                ? 'wake word unavailable · double-click to talk · check Voice settings'
          : activation === 'armed'
            ? wakePhrase
              ? `“${wakePhrase}”`
              : 'listening for wake word'
            : activation === 'listening' || micListening
              ? 'listening…'
              : 'double-click to talk');

  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className={
        'pointer-events-none fixed left-1/2 -translate-x-1/2 z-10 text-fg-body text-label font-mono tracking-wide text-center px-4 ' +
        (text === delegationText ? 'animate-pulse' : '')
      }
      style={{ bottom: 'calc(2rem + env(safe-area-inset-bottom, 0px))' }}
    >
      {text}
    </div>
  );
}
