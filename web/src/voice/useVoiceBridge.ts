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

import { invoke } from '@tauri-apps/api/core';
import { useEffect, useRef } from 'react';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { voiceStore, type VoiceSnapshot } from './state';
import { applyConnectionSignal, applyNativeAudioStatus } from './connection';
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
const MAX_PENDING_MIRRORS = 256;
let pendingStructuredProgress: {
  delegateId: string;
  text: string;
}[] = [];

function clearDelegateLifecycle(patch: Partial<VoiceSnapshot> = {}): void {
  delegateLifecycle = initialDelegateLifecycle();
  pendingStructuredProgress = [];
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
  if (applyConnectionSignal(event)) {
    // The bounded SSE bus has no replay cursor. Retain native mic truth but
    // discard old task presentation until fresh authoritative events arrive.
    clearDelegateLifecycle();
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
          ...(delegateLifecycle.activeTaskKey === null ? {
            delegationTaskKey: null,
            delegationProgress: null,
            delegationOutcome: null,
          } : {}),
        });
      } else if (ev === 'end') {
        voiceStore.update({
          activeToolCall: null,
          ...(delegateLifecycle.activeTaskKey === null ? {
            delegationTaskKey: null,
            delegationProgress: null,
            delegationOutcome: voiceStore.getSnapshot().delegationOutcome
              ?? (parsed.outcome as 'success' | 'error' | undefined)
              ?? 'success',
          } : {}),
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
        const structuredMirrorIndex = pendingStructuredProgress.findIndex(
          (progress) => progress.delegateId === source && progress.text === text,
        );
        if (structuredMirrorIndex !== -1) {
          // The task-keyed structured reducer already decided whether this
          // update owns the visible rail. Never let its task-blind compatibility
          // mirror reverse that decision; suppress this one exact pair only.
          pendingStructuredProgress.splice(structuredMirrorIndex, 1);
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
      const statusState = typeof parsed.state === 'string' ? parsed.state : '';
      const rawText = typeof parsed.text === 'string' ? boundedSseText(parsed.text, 1024) : '';
      const delegateId = typeof parsed.delegate_id === 'string'
        ? boundedSseText(parsed.delegate_id, 256)
        : '';
      if (
        event === 'delegate.status'
        && rawText
        && delegateId
        && !['completed', 'failed', 'canceled'].includes(statusState)
      ) {
        pendingStructuredProgress.push({
          delegateId,
          text: rawText,
        });
        if (pendingStructuredProgress.length > MAX_PENDING_MIRRORS) {
          pendingStructuredProgress.shift();
        }
      }
      if (!presentation) break;
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
  const unlistenAudioRef = useRef<UnlistenFn | null>(null);
  const unlistenWakeRef = useRef<UnlistenFn | null>(null);

  useEffect(() => {
    let cancelled = false;
    let connectionEventSeen = false;

    listen<SsePayload>('orbis-sse', (e) => {
      if (cancelled) return;
      if (e.payload.event.startsWith('__')) connectionEventSeen = true;
      handleSse(e.payload.event, e.payload.data);
    })
      .then(async (fn) => {
        if (cancelled) { fn(); return; }
        unlistenRef.current = fn;
        const connected = await invoke<boolean>('sse_connected');
        if (!cancelled && !connectionEventSeen) voiceStore.update({ connected });
      })
      .catch(() => {
        // listen() unavailable (non-Tauri dev) — voice state stays idle.
      });

    // Keep the retained wake snapshot until native readiness arrives. Detector
    // readiness is independent of socket/capture readiness; it cannot establish
    // a live listening turn after an observed native loss.
    let wakeEventSeen = false;
    let retainedWake: { state?: string; phrase?: string } | null = null;
    let nativeLossSeen = false;
    const applyWake = (payload: { state?: string; phrase?: string }) => {
      retainedWake = payload;
      const s = payload?.state;
      const audio = voiceStore.getSnapshot().nativeAudio;
      const ready = audio?.socket_connected && audio?.capture_alive;
      const activation = s === 'starting' || s === 'failed'
        ? s
        : ready && (s === 'armed' || s === 'listening') ? s : null;
      voiceStore.update({ activation, wakePhrase: payload?.phrase ?? null });
    };
    const applyAudio = (status: NonNullable<VoiceSnapshot['nativeAudio']>) => {
      applyNativeAudioStatus(status);
      if (!status.socket_connected || !status.capture_alive) {
        nativeLossSeen = true;
      } else if (retainedWake && (retainedWake.state !== 'listening' || !nativeLossSeen)) {
        applyWake(retainedWake);
      }
    };

    // Register first, then read retained truth: mounting an event listener
    // says nothing about whether the sidecar or microphone is actually alive.
    let audioEventSeen = false;
    listen<NonNullable<VoiceSnapshot['nativeAudio']>>('orbis-audio-status', (e) => {
      if (cancelled) return;
      audioEventSeen = true;
      applyAudio(e.payload);
    }).then(async (fn) => {
      if (cancelled) { fn(); return; }
      unlistenAudioRef.current = fn;
      const status = await invoke<NonNullable<VoiceSnapshot['nativeAudio']>>('audio_status');
      if (!cancelled && !audioEventSeen) applyAudio(status);
    }).catch(() => {});

    // Subscribe before reading retained state: a detector can warm up before
    // the UI mounts. An event received during the read beats the older snapshot.
    listen<{ state?: string; phrase?: string }>('wake-state', (e) => {
      if (cancelled) return;
      wakeEventSeen = true;
      applyWake(e.payload);
    })
      .then(async (fn) => {
        if (cancelled) { fn(); return; }
        unlistenWakeRef.current = fn;
        const retained = await invoke<{ state?: string; phrase?: string } | null>('get_wake_state');
        if (!cancelled && !wakeEventSeen && retained) applyWake(retained);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
      if (unlistenRef.current) {
        unlistenRef.current();
        unlistenRef.current = null;
      }
      if (unlistenAudioRef.current) {
        unlistenAudioRef.current();
        unlistenAudioRef.current = null;
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
