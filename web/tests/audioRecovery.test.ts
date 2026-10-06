import { beforeEach, describe, expect, test } from 'bun:test';
import { voiceStore } from '../src/voice/state';
import { applyConnectionSignal, applyNativeAudioStatus } from '../src/voice/connection';
import { effectiveVoiceLifecycle, voiceIsReady } from '../src/voice/lifecycle';

beforeEach(() => voiceStore.reset());
const running = { state: 'running' as const, detail: 'Voice pipeline ready' };

describe('native audio recovery truth', () => {
  test('mounting starts disconnected; only a real connection signal marks ready', () => {
    expect(voiceStore.getSnapshot().connected).toBeFalse();
    expect(applyConnectionSignal('transcript')).toBeFalse();
    expect(voiceStore.getSnapshot().connected).toBeFalse();
    applyConnectionSignal('__connected');
    expect(voiceStore.getSnapshot().connected).toBeTrue();
  });

  test('SSE loss clears stale presentation and preserves native listening truth', () => {
    voiceStore.update({ connected: true, state: 'speaking', micListening: true,
      activation: 'listening', activeToolCall: { name: 'delegate_to', args: {} }, sessionId: 'old' });
    applyConnectionSignal('__disconnected');
    const disconnected = voiceStore.getSnapshot();
    expect(disconnected.connected).toBeFalse();
    expect(disconnected.micListening).toBeTrue();
    expect(disconnected.activation).toBe('listening');
    expect(disconnected.activeToolCall).toBeNull();
    expect(disconnected.sessionId).toBe('old');
    expect(disconnected.state).toBe('idle');
    applyConnectionSignal('__connected');
    expect(voiceStore.getSnapshot().micListening).toBeTrue();
  });

  test('post-ready backend loss persists an actionable failure', () => {
    voiceStore.update({ voiceLifecycle: running, connected: true, micListening: true, activation: 'listening' });
    applyConnectionSignal('__backend_lost');
    expect(voiceStore.getSnapshot().voiceLifecycle?.action).toBe('relaunch_required');
    expect(voiceStore.getSnapshot().micListening).toBeFalse();
    expect(voiceStore.getSnapshot().activation).toBeNull();
    expect(voiceIsReady(voiceStore.getSnapshot().voiceLifecycle)).toBeFalse();
  });

  test('a live pipeline with dead capture is unavailable and offers relaunch', () => {
    const lifecycle = effectiveVoiceLifecycle(running, { socket_connected: true,
      capture_alive: false, detail: 'Microphone input stopped; relaunch ORBIS', relaunch_required: true });
    expect(voiceIsReady(lifecycle)).toBeFalse();
    expect(lifecycle?.action).toBe('relaunch_required');
    expect(lifecycle?.detail).toContain('Microphone input stopped');
  });

  test('retained capture failure clears an old mic turn on mount', () => {
    voiceStore.update({ micListening: true, activation: 'listening', state: 'speaking' });
    applyNativeAudioStatus({ socket_connected: true, capture_alive: false,
      detail: 'Microphone input stopped; relaunch ORBIS', relaunch_required: true });
    expect(voiceStore.getSnapshot().micListening).toBeFalse();
    expect(voiceStore.getSnapshot().activation).toBeNull();
    expect(voiceStore.getSnapshot().state).toBe('idle');
  });

  test('unknown native health cannot claim readiness', () => {
    expect(voiceIsReady(effectiveVoiceLifecycle(running, null))).toBeFalse();
  });

  test('socket acceptance alone does not mean callback delivery', () => {
    const lifecycle = effectiveVoiceLifecycle(running, { socket_connected: true,
      capture_alive: false, detail: 'Starting native audio…', relaunch_required: false });
    expect(lifecycle?.state).toBe('starting');
    expect(lifecycle?.action).toBeUndefined();
  });

  test('terminal native failure overrides a retryable model failure', () => {
    const failed = { state: 'failed' as const, detail: 'Voice models failed to load', action: 'retry' as const };
    const result = effectiveVoiceLifecycle(failed, { socket_connected: false,
      capture_alive: false, detail: 'Native audio could not start; relaunch ORBIS', relaunch_required: true });
    expect(result?.action).toBe('relaunch_required');
    expect(result?.detail).toContain('Native audio could not start');
  });

  test('healthy audio does not hide model startup failure or turn on the mic', () => {
    const failed = { state: 'failed' as const, detail: 'Voice models failed to load', action: 'retry' as const };
    expect(effectiveVoiceLifecycle(failed, { socket_connected: true,
      capture_alive: true, detail: '', relaunch_required: false })).toEqual(failed);
    expect(effectiveVoiceLifecycle(running, { socket_connected: true,
      capture_alive: true, detail: '', relaunch_required: false })).toEqual(running);
    expect(voiceStore.getSnapshot().micListening).toBeFalse();
  });
});
