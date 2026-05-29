/**
 * useVoiceBridge — SSE bridge between the Python sidecar and voiceStore.
 *
 * Opens an EventSource on /api/events and translates server-sent state
 * transitions into voiceStore updates so the orb, status pill, and any
 * plugin reading voiceStore stay in sync with the pipeline.
 *
 * Lifecycle
 * ---------
 * - EventSource opens on mount.
 * - On error: reconnects with exponential backoff (1 s → 2 s → 4 s … max 30 s).
 * - On unmount: EventSource is closed.
 *
 * Events consumed
 * ---------------
 *   bot-state  { state: 'idle'|'listening'|'thinking'|'speaking' }
 *   transcript { source: 'user'|'bot', text: string, final: boolean }
 *   session    { event: 'start'|'end', session_id?: string }
 *   tool-call  { event: 'start'|'end', name?, args?, outcome? }
 *   delegation-progress { type, source, text }
 *
 * Pre-2026-04-28 this was named useNativeBridge and gated behind
 * voiceStore.audioTransport === 'native'. The web/PWA / WebRTC path
 * was dropped (DECISIONS.md amendment of that date) and SSE is now
 * the only path; the gate and rename followed.
 */

import { useCallback, useEffect, useRef } from 'react';
import { voiceStore, type VoiceSnapshot } from './state';

const MIN_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 30_000;
const BACKOFF_FACTOR = 2;

export function useVoiceBridge(): void {
  const backoffRef = useRef(MIN_BACKOFF_MS);
  const esRef = useRef<EventSource | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const close = useCallback(() => {
    clearTimer();
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
  }, []);

  const open = useCallback(() => {
    if (esRef.current) return; // already open

    const es = new EventSource('/api/events');
    esRef.current = es;

    es.addEventListener('open', () => {
      backoffRef.current = MIN_BACKOFF_MS; // reset on successful open
      voiceStore.update({ connected: true });
    });

    es.addEventListener('bot-state', (e: MessageEvent) => {
      try {
        const { state } = JSON.parse(e.data) as { state: VoiceSnapshot['state'] };
        if (state) voiceStore.update({ state });
      } catch { /* malformed — ignore */ }
    });

    es.addEventListener('transcript', (e: MessageEvent) => {
      try {
        const { source, text } = JSON.parse(e.data) as {
          source: 'user' | 'bot';
          text: string;
          final: boolean;
        };
        if (source === 'user' && text) voiceStore.update({ lastUserTranscript: text });
        else if (source === 'bot' && text) voiceStore.update({ lastBotText: text });
      } catch { /* malformed — ignore */ }
    });

    es.addEventListener('session', (e: MessageEvent) => {
      try {
        const { event: ev, session_id } = JSON.parse(e.data) as {
          event: 'start' | 'end';
          session_id?: string;
        };
        if (ev === 'start') {
          voiceStore.update({
            connected: true,
            sessionId: session_id ?? null,
            state: 'idle',
          });
        } else if (ev === 'end') {
          voiceStore.update({ state: 'idle', sessionId: null });
        }
      } catch { /* malformed — ignore */ }
    });

    es.addEventListener('tool-call', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as {
          event?: 'start' | 'end';
          name?: string;
          args?: unknown;
          outcome?: 'success' | 'error';
        };
        if (data.event === 'start' && data.name) {
          voiceStore.update({
            activeToolCall: { name: data.name, args: parseArgs(data.args) },
            delegationProgress: null,
            delegationOutcome: null,
          });
        } else if (data.event === 'end') {
          voiceStore.update({
            activeToolCall: null,
            delegationProgress: null,
            delegationOutcome: data.outcome ?? 'success',
          });
        }
      } catch { /* malformed — ignore */ }
    });

    es.addEventListener('delegation-progress', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as { text?: string };
        if (typeof data.text === 'string') {
          voiceStore.update({ delegationProgress: data.text });
        }
      } catch { /* malformed — ignore */ }
    });

    es.addEventListener('error', () => {
      voiceStore.update({ connected: false, state: 'idle' });
      es.close();
      esRef.current = null;

      const delay = backoffRef.current;
      backoffRef.current = Math.min(backoffRef.current * BACKOFF_FACTOR, MAX_BACKOFF_MS);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        open();
      }, delay);
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    open();
    return close;
  }, [open, close]);
}

function parseArgs(args: unknown): unknown {
  if (typeof args !== 'string') return args;
  try {
    return JSON.parse(args);
  } catch {
    return args;
  }
}
