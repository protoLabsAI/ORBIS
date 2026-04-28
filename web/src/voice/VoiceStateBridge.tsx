import { RTVIEvent } from '@pipecat-ai/client-js';
import { useRTVIClientEvent, usePipecatClientTransportState } from '@pipecat-ai/client-react';
import { useEffect } from 'react';
import { voiceStore } from './state';
import { useNativeBridge } from './useNativeBridge';

/**
 * Invisible component — subscribes to RTVI events and drives the
 * derived voiceStore. Mount once, inside PipecatClientProvider.
 *
 * State-machine mapping (WebRTC / RTVI path):
 *   UserStartedSpeaking        → listening
 *   BotLlmStarted              → thinking
 *   BotStartedSpeaking         → speaking
 *   BotStoppedSpeaking         → idle
 *
 * Native audio path:
 *   On mount, fetches /healthz to detect AUDIO_TRANSPORT=native.
 *   If native, useNativeBridge() opens an EventSource on /api/events and
 *   translates SSE events into the same voiceStore updates, so the orb
 *   and status pill behave identically regardless of audio backend.
 */
export function VoiceStateBridge() {
  const transportState = usePipecatClientTransportState();

  // Detect audio transport mode once on mount by reading /healthz.
  // This is a one-shot fetch — AUDIO_TRANSPORT never changes at runtime.
  useEffect(() => {
    fetch('/healthz')
      .then((r) => r.json())
      .then((data) => {
        const transport = data?.audio?.transport ?? 'webrtc';
        voiceStore.update({
          audioTransport: transport === 'native' ? 'native' : 'webrtc',
        });
      })
      .catch(() => {
        // Server not ready yet or CORS issue — keep default 'webrtc'.
      });
  }, []);

  // Phase 5: open the SSE bridge in native mode.
  // useNativeBridge is a no-op when audioTransport !== 'native'.
  useNativeBridge();

  // Transport-level state flows into the snapshot (WebRTC path only).
  // In native mode, transportState stays 'disconnected' (no WebRTC) — the
  // SSE bridge sets connected=true / transportState='ready' independently.
  useEffect(() => {
    voiceStore.update({
      transportState,
      connected: transportState === 'ready' || transportState === 'connected',
    });
    if (transportState === 'disconnected') {
      // Only reset connected to false in WebRTC mode; in native mode the
      // SSE bridge owns the connected flag.
      const snap = voiceStore.getSnapshot();
      if (snap.audioTransport !== 'native') {
        voiceStore.update({ state: 'idle' });
      }
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
