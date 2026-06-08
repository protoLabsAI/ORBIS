import { useEffect, useRef, useSyncExternalStore } from 'react';
import { useVoiceStateSelector } from '@/voice/hooks';
import { statusBus } from '@/shared/statusBus';

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
  const activeToolCall = useVoiceStateSelector((s) => s.activeToolCall);
  const delegationOutcome = useVoiceStateSelector((s) => s.delegationOutcome);
  const lastOutcomeRef = useRef<typeof delegationOutcome>(null);
  const externalTransient = useSyncExternalStore(
    statusBus.subscribe,
    statusBus.getSnapshot,
  );
  const delegationText = activeToolCall
    ? formatActiveToolCall(activeToolCall)
    : null;

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
  const text = externalTransient?.text
    ?? delegationText
    ?? (!connected
      ? 'starting up…'
      : voiceState === 'speaking'
        ? 'speaking…'
        : voiceState === 'thinking'
          ? 'thinking…'
          : activation === 'armed'
            ? wakePhrase
              ? `“${wakePhrase}”`
              : 'listening for wake word'
            : activation === 'listening' || micListening
              ? 'listening…'
              : 'double-click to talk');

  return (
    <div
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

function formatActiveToolCall(
  call: { name: string; args: unknown },
): string {
  if (call.name === 'delegate_to') {
    const target = readDelegateTarget(call.args);
    // Just the target — the per-tool stream (which can be 6 tools deep in
    // parallel) goes to the logs panel, not smeared across the status line.
    return target ? `asking ${target}…` : 'delegating…';
  }
  if (call.name === 'adjust_personality') {
    return 'adjusting personality…';
  }
  if (call.name === 'check_inbox') {
    return 'checking inbox…';
  }
  return `${call.name}…`;
}

function readDelegateTarget(args: unknown): string | null {
  if (typeof args !== 'object' || args === null) return null;
  const target = (args as { target?: unknown }).target;
  return typeof target === 'string' && target.length > 0 ? target : null;
}
