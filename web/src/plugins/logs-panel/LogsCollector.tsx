import { useEffect } from 'react';
import { RTVIEvent } from '@pipecat-ai/client-js';
import { usePipecatClient, useRTVIClientEvent, usePipecatClientTransportState } from '@pipecat-ai/client-react';
import { logBus } from '@/shared/logBus';

/**
 * Silent component — mounted at the App root. Subscribes to a curated
 * set of RTVI client events and forwards them to logBus so the Logs
 * drawer tab can tail them. Lives outside the Logs panel itself so the
 * buffer captures events before the user opens the tab (you can scroll
 * back through what already happened).
 *
 * Curated, not wildcard: RTVI fires a *lot* of frames per turn (audio
 * chunks especially) and dumping them all would drown out the
 * higher-signal lifecycle frames. Add events here as you find them
 * useful for debugging.
 */
export function LogsCollector() {
  const client = usePipecatClient();
  const transportState = usePipecatClientTransportState();

  // Transport-state log: emitted via effect (not a Pipecat hook) since
  // useRTVIClientTransportState updates as a regular value, not an event.
  useEffect(() => {
    logBus.push({
      source: 'webrtc',
      level: 'info',
      message: `transport: ${transportState}`,
    });
  }, [transportState]);

  useRTVIClientEvent(RTVIEvent.BotReady, () =>
    logBus.push({ source: 'rtvi', level: 'info', message: 'BotReady' }),
  );
  useRTVIClientEvent(RTVIEvent.Disconnected, () =>
    logBus.push({ source: 'rtvi', level: 'info', message: 'Disconnected' }),
  );
  useRTVIClientEvent(RTVIEvent.Error, (m: unknown) =>
    logBus.push({
      source: 'rtvi',
      level: 'error',
      message: `Error: ${typeof m === 'object' ? JSON.stringify(m) : String(m)}`,
    }),
  );
  useRTVIClientEvent(RTVIEvent.UserStartedSpeaking, () =>
    logBus.push({ source: 'rtvi', level: 'debug', message: 'UserStartedSpeaking' }),
  );
  useRTVIClientEvent(RTVIEvent.UserStoppedSpeaking, () =>
    logBus.push({ source: 'rtvi', level: 'debug', message: 'UserStoppedSpeaking' }),
  );
  useRTVIClientEvent(RTVIEvent.UserTranscript, (d: unknown) => {
    const t = d as { text?: string; final?: boolean } | undefined;
    if (t?.final) {
      logBus.push({
        source: 'rtvi',
        level: 'info',
        message: `UserTranscript: ${t.text ?? ''}`,
      });
    }
  });
  useRTVIClientEvent(RTVIEvent.BotLlmStarted, () =>
    logBus.push({ source: 'rtvi', level: 'debug', message: 'BotLlmStarted' }),
  );
  useRTVIClientEvent(RTVIEvent.BotStartedSpeaking, () =>
    logBus.push({ source: 'rtvi', level: 'debug', message: 'BotStartedSpeaking' }),
  );
  useRTVIClientEvent(RTVIEvent.BotStoppedSpeaking, () =>
    logBus.push({ source: 'rtvi', level: 'debug', message: 'BotStoppedSpeaking' }),
  );
  useRTVIClientEvent(RTVIEvent.BotTranscript, (d: unknown) => {
    const t = d as { text?: string } | undefined;
    if (t?.text) {
      logBus.push({
        source: 'rtvi',
        level: 'info',
        message: `BotTranscript: ${t.text}`,
      });
    }
  });
  useRTVIClientEvent(RTVIEvent.LLMFunctionCallStarted, (d: unknown) => {
    const t = d as { function_name?: string } | undefined;
    logBus.push({
      source: 'rtvi',
      level: 'info',
      message: `tool: ${t?.function_name ?? '?'}`,
    });
  });

  // Bind a one-shot install of fetch logging the first time the client
  // is non-null. Keep it idempotent so HMR doesn't double-wrap.
  useEffect(() => {
    if (!client) return;
    if ((window as unknown as { __orbis_fetch_wrapped?: boolean }).__orbis_fetch_wrapped) return;
    (window as unknown as { __orbis_fetch_wrapped?: boolean }).__orbis_fetch_wrapped = true;
    const orig = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url;
      const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
      // Skip noise: log /api/* but not Vite HMR / static assets.
      const isApi = url.includes('/api/');
      const t0 = isApi ? performance.now() : 0;
      try {
        const res = await orig(input, init);
        if (isApi) {
          const dt = Math.round(performance.now() - t0);
          logBus.push({
            source: 'fetch',
            level: res.ok ? 'info' : 'warn',
            message: `${method} ${url} → ${res.status} (${dt}ms)`,
          });
        }
        return res;
      } catch (e) {
        if (isApi) {
          logBus.push({
            source: 'fetch',
            level: 'error',
            message: `${method} ${url} → ${(e as Error).message}`,
          });
        }
        throw e;
      }
    };
  }, [client]);

  return null;
}
