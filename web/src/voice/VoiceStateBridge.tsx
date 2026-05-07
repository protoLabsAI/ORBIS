import { RTVIEvent } from '@pipecat-ai/client-js';
import { useRTVIClientEvent, usePipecatClientTransportState } from '@pipecat-ai/client-react';
import { useEffect } from 'react';
import { voiceStore, type DeviceErrorType } from './state';

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
      // `error` is pipecat's signal that the transport itself failed
      // (handshake error, data-channel drop). A clean user-initiated
      // disconnect goes to `disconnected` without passing through
      // `error`, so we don't show the banner in that case. Reaching
      // `ready`/`connected` again clears the flag so re-connect after
      // a network blip dismisses the banner without user action.
      connectionError: transportState === 'error',
    });
    if (transportState === 'disconnected') {
      voiceStore.update({ state: 'idle' });
    }
  }, [transportState]);

  // Device-level errors — mic permission denied, no mic detected, mic
  // already in use by another app, etc. Pipecat's DeviceError carries
  // a typed discriminator we forward to the banner so it can render
  // type-specific copy + recovery hints.
  useRTVIClientEvent(RTVIEvent.DeviceError, (err: unknown) => {
    const e = err as { type?: string; message?: string } | undefined;
    if (!e) return;
    voiceStore.update({
      deviceError: {
        type: (e.type as DeviceErrorType) || 'unknown',
        message: e.message,
      },
    });
  });

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

  // Started fires first (function_name only), InProgress fires when
  // arguments arrive. We populate from Started so the pill flashes up
  // immediately, then upgrade with the args (target name) as soon as
  // InProgress lands. Some pipecat builds skip Started and only fire
  // InProgress; populating from both keeps us covered.
  useRTVIClientEvent(RTVIEvent.LLMFunctionCallStarted, (d: unknown) => {
    const data = d as { function_name?: string } | undefined;
    if (!data?.function_name) return;
    voiceStore.update({
      activeToolCall: { name: data.function_name, args: undefined },
      delegationProgress: null,
      delegationOutcome: null,
    });
  });

  useRTVIClientEvent(RTVIEvent.LLMFunctionCallInProgress, (d: unknown) => {
    const data = d as { function_name?: string; arguments?: unknown } | undefined;
    if (!data?.function_name) return;
    voiceStore.update({
      activeToolCall: { name: data.function_name, args: data.arguments },
      // Don't clear delegationProgress here — InProgress can fire AFTER
      // Started + a progress message has already arrived, and we don't
      // want to wipe a freshly-rendered subtitle.
      delegationOutcome: null,
    });
  });

  // Stopped carries `cancelled` and `result`. We classify the outcome
  // for the orb / mood driver: cancelled → error (user or system aborted),
  // result starts with the documented unreachable-error pattern from
  // agent/tools.py:252-256 → error, anything else → success. Mood signal
  // resets on the NEXT call's Started event.
  useRTVIClientEvent(RTVIEvent.LLMFunctionCallStopped, (d: unknown) => {
    const data = d as { cancelled?: boolean; result?: unknown } | undefined;
    voiceStore.update({
      activeToolCall: null,
      delegationProgress: null,
      delegationOutcome: classifyOutcome(data),
    });
  });

  // Delivery progress mirror — backend's DeliveryController.speak_now
  // emits a typed `delegation-progress` server message alongside the
  // verbal narration so the "Asking ava…" pill can render the same
  // text under the headline. Verbal channel is the source of truth;
  // this is best-effort and silently no-ops if the message is malformed.
  useRTVIClientEvent(RTVIEvent.ServerMessage, (msg: unknown) => {
    const m = msg as { type?: string; source?: string; text?: string } | undefined;
    if (!m || m.type !== 'delegation-progress') return;
    if (typeof m.text !== 'string') return;
    voiceStore.update({ delegationProgress: m.text });
  });

  return null;
}

/**
 * Classify a function-call outcome for the visual / mood reaction
 * channel. Errors win over successes because the spoken result
 * (e.g. "Couldn't reach ava") is the user-facing signal we want to
 * mirror visually — a clean "result" body is the silent happy path.
 *
 * Patterns matched (from agent/tools.py:248-256):
 *   - "I need both a target and a question to delegate."
 *   - "I don't know a delegate named '{x}'. Available: …"
 *   - "Couldn't reach {target}: …"
 *   - "Delegation to {target} errored: …"
 */
function classifyOutcome(
  data: { cancelled?: boolean; result?: unknown } | undefined,
): 'success' | 'error' {
  if (!data) return 'success';
  if (data.cancelled) return 'error';
  const result = data.result;
  if (typeof result === 'string') {
    const errorMarkers = [
      "couldn't reach",
      'errored:',
      "i don't know a delegate",
      'i need both a target',
    ];
    const low = result.toLowerCase();
    if (errorMarkers.some((m) => low.includes(m))) return 'error';
  }
  return 'success';
}
