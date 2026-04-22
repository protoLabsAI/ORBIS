import { RTVIEvent } from '@pipecat-ai/client-js';
import { useRTVIClientEvent, usePipecatClientTransportState } from '@pipecat-ai/client-react';
import { useEffect } from 'react';
import { voiceStore } from './state';

/**
 * Invisible component — subscribes to RTVI events and drives the
 * derived voiceStore. Mount once, inside PipecatClientProvider.
 *
 * State-machine mapping:
 *   UserStartedSpeaking        → listening
 *   BotLlmStarted              → thinking
 *   BotStartedSpeaking         → speaking
 *   BotStoppedSpeaking + user-silent → idle (resolved by settle)
 */
export function VoiceStateBridge() {
  const transportState = usePipecatClientTransportState();

  // Transport-level state flows into the snapshot.
  useEffect(() => {
    voiceStore.update({
      transportState,
      connected: transportState === 'ready' || transportState === 'connected',
    });
    if (transportState === 'disconnected') {
      voiceStore.update({ state: 'idle' });
    }
  }, [transportState]);

  useRTVIClientEvent(RTVIEvent.BotReady, () => {
    voiceStore.update({ state: 'idle' });
  });

  useRTVIClientEvent(RTVIEvent.UserStartedSpeaking, () => {
    voiceStore.update({ state: 'listening' });
  });

  useRTVIClientEvent(RTVIEvent.UserStoppedSpeaking, () => {
    // Do not flip to 'idle' immediately — the bot may start thinking/speaking
    // within milliseconds. Leave the state where it is; the next event wins.
  });

  useRTVIClientEvent(RTVIEvent.UserTranscript, (d: unknown) => {
    const data = d as { text?: string; final?: boolean } | undefined;
    if (data?.text && data.final) voiceStore.update({ lastUserTranscript: data.text });
  });

  useRTVIClientEvent(RTVIEvent.BotLlmStarted, () => {
    voiceStore.update({ state: 'thinking' });
  });

  useRTVIClientEvent(RTVIEvent.BotStartedSpeaking, () => {
    voiceStore.update({ state: 'speaking' });
  });

  useRTVIClientEvent(RTVIEvent.BotStoppedSpeaking, () => {
    voiceStore.update({ state: 'idle' });
  });

  useRTVIClientEvent(RTVIEvent.BotTranscript, (d: unknown) => {
    const data = d as { text?: string } | undefined;
    if (data?.text) voiceStore.update({ lastBotText: data.text });
  });

  useRTVIClientEvent(RTVIEvent.LLMFunctionCallStarted, (d: unknown) => {
    const data = d as { function_name?: string; args?: unknown } | undefined;
    if (data?.function_name) {
      voiceStore.update({ activeToolCall: { name: data.function_name, args: data.args } });
    }
  });

  useRTVIClientEvent(RTVIEvent.LLMFunctionCallStopped, () => {
    voiceStore.update({ activeToolCall: null });
  });

  return null;
}
