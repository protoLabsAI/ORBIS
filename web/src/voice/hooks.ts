import { useCallback, useSyncExternalStore } from 'react';
import { RTVIEvent } from '@pipecat-ai/client-js';
import {
  useRTVIClientEvent,
  usePipecatClient,
  usePipecatClientTransportState,
} from '@pipecat-ai/client-react';
import { voiceStore, type VoiceSnapshot } from './state';

/**
 * Full derived snapshot. Re-renders on every store epoch tick.
 */
export function useVoiceState(): VoiceSnapshot {
  return useSyncExternalStore(voiceStore.subscribe, voiceStore.getSnapshot, voiceStore.getSnapshot);
}

/**
 * Select a slice of the snapshot. Re-renders only when the selected
 * value changes by referential equality. Use for perf-sensitive
 * consumers (the orb reads only `state`).
 */
export function useVoiceStateSelector<T>(selector: (s: VoiceSnapshot) => T): T {
  // useSyncExternalStore's selector form.
  const get = useCallback(() => selector(voiceStore.getSnapshot()), [selector]);
  return useSyncExternalStore(voiceStore.subscribe, get, get);
}

/**
 * Imperative API wrapping the underlying PipecatClient.
 */
export function useVoiceSession() {
  const client = usePipecatClient();
  const transportState = usePipecatClientTransportState();
  return {
    connect: () => client?.connect(),
    disconnect: () => client?.disconnect(),
    sendClientMessage: (type: string, data?: unknown) => client?.sendClientMessage(type, data),
    transportState,
    ready: transportState === 'ready',
  };
}

/**
 * Narrow helper — subscribe to the typical bot-turn lifecycle in one call.
 * Handlers are optional; pipecat's own useRTVIClientEvent handles unmount.
 */
export function useBotTurnEvents(handlers: {
  onLLMStarted?: () => void;
  onLLMStopped?: () => void;
  onTTSStarted?: () => void;
  onTTSStopped?: () => void;
  onStartedSpeaking?: () => void;
  onStoppedSpeaking?: () => void;
}) {
  useRTVIClientEvent(RTVIEvent.BotLlmStarted, handlers.onLLMStarted ?? noop);
  useRTVIClientEvent(RTVIEvent.BotLlmStopped, handlers.onLLMStopped ?? noop);
  useRTVIClientEvent(RTVIEvent.BotTtsStarted, handlers.onTTSStarted ?? noop);
  useRTVIClientEvent(RTVIEvent.BotTtsStopped, handlers.onTTSStopped ?? noop);
  useRTVIClientEvent(RTVIEvent.BotStartedSpeaking, handlers.onStartedSpeaking ?? noop);
  useRTVIClientEvent(RTVIEvent.BotStoppedSpeaking, handlers.onStoppedSpeaking ?? noop);
}

export function useUserTurnEvents(handlers: {
  onStartedSpeaking?: () => void;
  onStoppedSpeaking?: () => void;
  onTranscription?: (text: string, final: boolean) => void;
}) {
  useRTVIClientEvent(RTVIEvent.UserStartedSpeaking, handlers.onStartedSpeaking ?? noop);
  useRTVIClientEvent(RTVIEvent.UserStoppedSpeaking, handlers.onStoppedSpeaking ?? noop);
  useRTVIClientEvent(
    RTVIEvent.UserTranscript,
    handlers.onTranscription
      ? (d) => handlers.onTranscription?.(d?.text ?? '', !!d?.final)
      : noop,
  );
}

function noop() {}
