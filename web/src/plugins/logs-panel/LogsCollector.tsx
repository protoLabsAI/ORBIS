import { useEffect, useRef } from 'react';
import { logBus } from '@/shared/logBus';
import { voiceStore, type VoiceSnapshot } from '@/voice/state';

/**
 * Silent component mounted at the app root. It mirrors high-signal native
 * SSE/voice state transitions into logBus so the Dev drawer can show what
 * already happened before the user opened the log.
 */
export function LogsCollector() {
  const prevRef = useRef<VoiceSnapshot | null>(null);

  useEffect(() => {
    const emit = () => {
      const snap = voiceStore.getSnapshot();
      const prev = prevRef.current;
      prevRef.current = snap;

      if (!prev || prev.connected !== snap.connected) {
        logBus.push({
          source: 'sse',
          level: snap.connected ? 'info' : 'warn',
          message: snap.connected ? 'connected' : 'disconnected',
        });
      }
      if (!prev || prev.state !== snap.state) {
        logBus.push({ source: 'voice', level: 'debug', message: `state: ${snap.state}` });
      }
      if (prev?.sessionId !== snap.sessionId) {
        logBus.push({
          source: 'voice',
          level: 'info',
          message: snap.sessionId ? `session: ${snap.sessionId}` : 'session ended',
        });
      }
      if (snap.lastUserTranscript && prev?.lastUserTranscript !== snap.lastUserTranscript) {
        logBus.push({
          source: 'voice',
          level: 'info',
          message: `user: ${snap.lastUserTranscript}`,
        });
      }
      if (snap.lastBotText && prev?.lastBotText !== snap.lastBotText) {
        logBus.push({ source: 'voice', level: 'info', message: `bot: ${snap.lastBotText}` });
      }
      if (snap.activeToolCall && prev?.activeToolCall !== snap.activeToolCall) {
        logBus.push({
          source: 'voice',
          level: 'info',
          message: `tool: ${snap.activeToolCall.name}`,
        });
      }
      if (snap.delegationProgress && prev?.delegationProgress !== snap.delegationProgress) {
        logBus.push({
          source: 'voice',
          level: 'info',
          message: snap.delegationProgress,
        });
      }
    };

    prevRef.current = voiceStore.getSnapshot();
    return voiceStore.subscribe(emit);
  }, []);

  return null;
}
