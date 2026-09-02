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
 *   delegate.status { delegate_id, task_id?, session_id?, state, text? }
 *   delegate.tool   { delegate_id, task_id?, session_id?, name, status }
 *   delegate.delta  { delegate_id, task_id?, session_id?, deltas }
 *   widget     { action: 'open'|'close', id, props? } — render_widget tool
 *   orb-config { variant?, palette?, params? } — set_orb_visual tool
 *   persona-switched { slug, name, applies, notes, viz? } — persona change
 *   __connected — synthetic, emitted by the bridge on (re)connect
 *
 * Pre-2026-04-28 this was useNativeBridge gated behind a WebRTC path;
 * pre-Tahoe it opened a browser EventSource directly. Both are gone.
 */

import { useEffect, useRef } from 'react';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { voiceStore, type VoiceSnapshot } from './state';
import { widgetWorkspace } from '../widgets/store';
import { applyParam, applyPreset, setVariant } from '../plugins/orb/broadcast';
import { logBus } from '../shared/logBus';
import {
  initialDelegateLifecycle,
  reduceDelegateEvent,
} from './delegateEvents';

interface SsePayload {
  event: string;
  data: string;
}

let delegateLifecycle = initialDelegateLifecycle();
let lastStructuredProgress: {
  delegateId: string;
  taskKey: string;
  text: string;
} | null = null;

function clearDelegateLifecycle(patch: Partial<VoiceSnapshot> = {}): void {
  delegateLifecycle = initialDelegateLifecycle();
  lastStructuredProgress = null;
  voiceStore.update({
    delegationTaskKey: null,
    delegationProgress: null,
    delegationOutcome: null,
    ...patch,
  });
}

function boundedSseText(value: string, maxBytes: number): string {
  const encoder = new TextEncoder();
  let bounded = new TextDecoder().decode(encoder.encode(value.trim()).slice(0, maxBytes));
  while (encoder.encode(bounded).length > maxBytes) bounded = bounded.slice(0, -1);
  return bounded;
}

export function handleSse(event: string, data: string): void {
  if (event === '__connected') {
    // The bounded SSE bus has no replay cursor. Reconnect cannot prove that a
    // previously visible task is still current, so clear rather than display a
    // stale progress rail until fresh authoritative events arrive.
    clearDelegateLifecycle({ connected: true });
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
        clearDelegateLifecycle({
          connected: true,
          sessionId: (parsed.session_id as string | undefined) ?? null,
          state: 'idle',
        });
      } else if (ev === 'end') {
        clearDelegateLifecycle({ state: 'idle', sessionId: null });
      }
      break;
    }
    case 'tool-call': {
      const ev = parsed.event as 'start' | 'end' | undefined;
      if (ev === 'start' && parsed.name) {
        voiceStore.update({
          activeToolCall: { name: String(parsed.name), args: parseArgs(parsed.args) },
          delegationTaskKey: null,
          delegationProgress: null,
          delegationOutcome: null,
        });
      } else if (ev === 'end') {
        voiceStore.update({
          activeToolCall: null,
          delegationTaskKey: null,
          delegationProgress: null,
          delegationOutcome: (parsed.outcome as 'success' | 'error' | undefined) ?? 'success',
        });
      }
      break;
    }
    case 'delegation-progress': {
      if (typeof parsed.text === 'string') {
        const text = boundedSseText(parsed.text, 1024);
        const source = typeof parsed.source === 'string'
          ? boundedSseText(parsed.source, 256)
          : '';
        const structuredMirror = lastStructuredProgress?.delegateId === source
          && lastStructuredProgress.text === text;
        if (structuredMirror) {
          // The task-keyed structured reducer already decided whether this
          // update owns the visible rail. Never let its task-blind compatibility
          // mirror reverse that decision; suppress this one exact pair only.
          lastStructuredProgress = null;
          break;
        }
        voiceStore.update({
          delegationProgress: source ? `${source}: ${text}` : text,
        });
        logBus.push({ source: 'delegate', level: 'info', message: text });
      }
      break;
    }
    case 'delegate.status':
    case 'delegate.tool':
    case 'delegate.delta': {
      const reduced = reduceDelegateEvent(delegateLifecycle, event, parsed);
      delegateLifecycle = reduced.lifecycle;
      const presentation = reduced.presentation;
      if (!presentation) break;
      const statusState = typeof parsed.state === 'string' ? parsed.state : '';
      if (
        event === 'delegate.status'
        && presentation.rawText
        && !['completed', 'failed', 'canceled'].includes(statusState)
      ) {
        lastStructuredProgress = {
          delegateId: presentation.delegateId,
          taskKey: presentation.taskKey,
          text: presentation.rawText,
        };
      }
      if (Object.keys(presentation.patch).length > 0) {
        voiceStore.update(presentation.patch);
      }
      logBus.push({ source: 'delegate', ...presentation.log });
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
    case 'orb-config': {
      // Voice-driven orb restyling (set_orb_visual tool): apply live so the
      // on-screen orb changes without a reload. Variant + palette swap; params
      // merge onto the current knobs.
      const variant = parsed.variant as string | undefined;
      const palette = parsed.palette as string | undefined;
      if (variant) setVariant(variant);
      if (palette) applyPreset(palette);
      const p = parsed.params;
      if (p && typeof p === 'object') {
        for (const [k, v] of Object.entries(p as Record<string, unknown>)) {
          applyParam(k, v);
        }
      }
      break;
    }
    case 'persona-switched': {
      // Persona switch (epic #611 P2): the backend hot-swapped prompt/
      // LLM/voice; the orb identity is applied here. Same apply shape as
      // orb-config so any switch source (picker, dialog, voice tool)
      // lands identically.
      const viz = parsed.viz as
        | { variant?: string; palette?: string; params?: Record<string, unknown> }
        | undefined;
      if (viz?.variant) setVariant(viz.variant);
      if (viz?.palette) applyPreset(viz.palette);
      if (viz?.params) {
        for (const [k, v] of Object.entries(viz.params)) {
          applyParam(k, v);
        }
      }
      break;
    }
  }
}

export function useVoiceBridge(): void {
  const unlistenRef = useRef<UnlistenFn | null>(null);
  const unlistenWakeRef = useRef<UnlistenFn | null>(null);

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

    // Wake-word activation state — emitted Rust-side (audio/wake_word.rs),
    // independent of the Python SSE bridge (it fires while the mic is muted,
    // before Python sees any audio). Payload: { state, phrase }.
    listen<{ state?: string; phrase?: string }>('wake-state', (e) => {
      const s = e.payload?.state;
      const a = s === 'armed' || s === 'listening' ? s : null;
      voiceStore.update({ activation: a, wakePhrase: e.payload?.phrase ?? null });
    })
      .then((fn) => {
        if (cancelled) {
          fn();
          return;
        }
        unlistenWakeRef.current = fn;
      })
      .catch(() => {});

    return () => {
      cancelled = true;
      if (unlistenRef.current) {
        unlistenRef.current();
        unlistenRef.current = null;
      }
      if (unlistenWakeRef.current) {
        unlistenWakeRef.current();
        unlistenWakeRef.current = null;
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
