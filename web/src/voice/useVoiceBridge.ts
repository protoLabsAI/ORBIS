/**
 * useVoiceBridge — bridges the Python sidecar's /api/events stream into
 * voiceStore so the orb, status pill, and any plugin reading voiceStore
 * stay in sync with the pipeline.
 *
 * Transport
 * ---------
 * macOS Tahoe's WKWebView won't reliably stream a (now cross-origin)
 * EventSource from the bundled tauri://localhost UI, so the Rust shell
 * consumes the SSE stream via reqwest and re-emits each message as a
 * Tauri `orbis-sse` event. We listen for those here. Reconnection /
 * backoff is handled Rust-side in `bridge_sse`.
 *
 * Events consumed (payload.event / payload.data JSON)
 * ---------------
 *   bot-state  { state: 'idle'|'listening'|'thinking'|'speaking' }
 *   transcript { source: 'user'|'bot', text: string, final: boolean }
 *   session    { event: 'start'|'end', session_id?: string }
 *   tool-call  { event: 'start'|'end', name?, args?, outcome? }
 *   delegation-progress { type, source, text }
 *   widget     { action: 'open'|'close', id, props? } — render_widget tool
 *   __connected — synthetic, emitted by the bridge on (re)connect
 *
 * Pre-2026-04-28 this was useNativeBridge gated behind a WebRTC path;
 * pre-Tahoe it opened a browser EventSource directly. Both are gone.
 */

import { useEffect, useRef } from 'react';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { voiceStore, type VoiceSnapshot } from './state';
import { widgetWorkspace } from '../widgets/store';

interface SsePayload {
  event: string;
  data: string;
}

function handleSse(event: string, data: string): void {
  if (event === '__connected') {
    voiceStore.update({ connected: true });
    return;
  }

  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(data) as Record<string, unknown>;
  } catch {
    return; // malformed — ignore
  }

  switch (event) {
    case 'bot-state': {
      const state = parsed.state as VoiceSnapshot['state'] | undefined;
      if (state) voiceStore.update({ state });
      break;
    }
    case 'transcript': {
      const source = parsed.source as 'user' | 'bot' | undefined;
      const text = parsed.text as string | undefined;
      if (source === 'user' && text) voiceStore.update({ lastUserTranscript: text });
      else if (source === 'bot' && text) voiceStore.update({ lastBotText: text });
      break;
    }
    case 'session': {
      const ev = parsed.event as 'start' | 'end' | undefined;
      if (ev === 'start') {
        voiceStore.update({
          connected: true,
          sessionId: (parsed.session_id as string | undefined) ?? null,
          state: 'idle',
        });
      } else if (ev === 'end') {
        voiceStore.update({ state: 'idle', sessionId: null });
      }
      break;
    }
    case 'tool-call': {
      const ev = parsed.event as 'start' | 'end' | undefined;
      if (ev === 'start' && parsed.name) {
        voiceStore.update({
          activeToolCall: { name: String(parsed.name), args: parseArgs(parsed.args) },
          delegationProgress: null,
          delegationOutcome: null,
        });
      } else if (ev === 'end') {
        voiceStore.update({
          activeToolCall: null,
          delegationProgress: null,
          delegationOutcome: (parsed.outcome as 'success' | 'error' | undefined) ?? 'success',
        });
      }
      break;
    }
    case 'delegation-progress': {
      if (typeof parsed.text === 'string') {
        voiceStore.update({ delegationProgress: parsed.text });
      }
      break;
    }
    case 'widget': {
      // Voice-driven widget control (render_widget tool): open/close + seed state.
      const id = parsed.id as string | undefined;
      if (!id) break;
      const action = (parsed.action as string | undefined) ?? 'open';
      if (action === 'close') {
        widgetWorkspace.close(id);
      } else {
        widgetWorkspace.openWidget(id);
        const props = parsed.props;
        if (props && typeof props === 'object') {
          widgetWorkspace.setProps(id, props as Record<string, unknown>);
        }
      }
      break;
    }
  }
}

export function useVoiceBridge(): void {
  const unlistenRef = useRef<UnlistenFn | null>(null);

  useEffect(() => {
    let cancelled = false;

    listen<SsePayload>('orbis-sse', (e) => {
      handleSse(e.payload.event, e.payload.data);
    })
      .then((fn) => {
        if (cancelled) {
          fn();
          return;
        }
        unlistenRef.current = fn;
        voiceStore.update({ connected: true });
      })
      .catch(() => {
        // listen() unavailable (non-Tauri dev) — voice state stays idle.
      });

    return () => {
      cancelled = true;
      if (unlistenRef.current) {
        unlistenRef.current();
        unlistenRef.current = null;
      }
      voiceStore.update({ connected: false });
    };
  }, []);
}

function parseArgs(args: unknown): unknown {
  if (typeof args !== 'string') return args;
  try {
    return JSON.parse(args);
  } catch {
    return args;
  }
}
