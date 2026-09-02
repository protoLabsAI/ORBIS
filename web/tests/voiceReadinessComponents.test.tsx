import { describe, expect, test } from 'bun:test';
import { renderToStaticMarkup } from 'react-dom/server';
import { VoiceMicButton, VoiceRecoveryNotice } from '../src/voice/components';
import type { VoiceLifecycle } from '../src/voice/lifecycle';
import { retryVoiceWithRefresh, toggleVoiceWhenReady } from '../src/voice/actions';

const callback = () => {};

function failed(action?: VoiceLifecycle['action']): VoiceLifecycle {
  return {
    state: 'failed',
    detail: 'Voice pipeline stopped unexpectedly',
    code: 'pipeline_stopped',
    action,
  };
}

describe('voice readiness components', () => {
  test('microphone interaction is disabled with an accessible reason until running', () => {
    const unavailable = renderToStaticMarkup(
      <VoiceMicButton
        ready={false}
        muted={false}
        unavailableText="Loading voice models…"
        onClick={callback}
      />,
    );
    expect(unavailable).toContain('disabled=""');
    expect(unavailable).toContain('aria-label="Loading voice models…"');
    expect(unavailable).toContain('aria-describedby="voice-mic-availability"');
    expect(unavailable).toContain('id="voice-mic-availability"');

    const running = renderToStaticMarkup(
      <VoiceMicButton
        ready
        muted={false}
        unavailableText=""
        onClick={callback}
      />,
    );
    expect(running).not.toContain('disabled=""');
    expect(running).toContain('aria-label="Mute microphone"');
    expect(running).not.toContain('aria-describedby');
  });

  test('retry is offered only for a retryable backend failure', () => {
    const markup = renderToStaticMarkup(
      <VoiceRecoveryNotice
        lifecycle={failed('retry')}
        busy={false}
        onRetry={callback}
        onRelaunch={callback}
      />,
    );
    expect(markup).toContain('role="alert"');
    expect(markup).toContain('Retry voice');
    expect(markup).not.toContain('Relaunch ORBIS');
  });

  test('post-connection failure offers relaunch and never retry', () => {
    const markup = renderToStaticMarkup(
      <VoiceRecoveryNotice
        lifecycle={failed('relaunch_required')}
        busy={false}
        onRetry={callback}
        onRelaunch={callback}
      />,
    );
    expect(markup).toContain('Relaunch ORBIS');
    expect(markup).not.toContain('Retry voice');
  });

  test('unknown recovery capability does not invent an action', () => {
    const markup = renderToStaticMarkup(
      <VoiceRecoveryNotice
        lifecycle={failed()}
        busy={false}
        onRetry={callback}
        onRelaunch={callback}
      />,
    );
    expect(markup).toContain('Voice pipeline stopped unexpectedly');
    expect(markup).not.toContain('<button');
  });

  test('command microphone action never reaches native commands before running', async () => {
    const calls: string[] = [];
    const toggled = await toggleVoiceWhenReady({
      readLifecycle: async () => ({ state: 'starting', detail: 'Starting voice…' }),
      readListening: async () => {
        calls.push('read');
        return false;
      },
      setListening: async () => { calls.push('set'); },
    });
    expect(toggled).toBeFalse();
    expect(calls).toEqual([]);
  });

  test('command microphone action toggles only after authoritative running health', async () => {
    const values: boolean[] = [];
    const toggled = await toggleVoiceWhenReady({
      readLifecycle: async () => ({ state: 'running', detail: 'Ready' }),
      readListening: async () => false,
      setListening: async (on) => { values.push(on); },
    });
    expect(toggled).toBeTrue();
    expect(values).toEqual([true]);
  });

  test('stale retry reconciles a relaunch-required lifecycle after 409', async () => {
    const applied: VoiceLifecycle[] = [];
    await expect(retryVoiceWithRefresh({
      retry: async () => { throw new Error('409 relaunch_required'); },
      readLifecycle: async () => failed('relaunch_required'),
      applyLifecycle: (lifecycle) => applied.push(lifecycle),
    })).rejects.toThrow('409 relaunch_required');
    expect(applied.map((lifecycle) => lifecycle.action)).toEqual(['relaunch_required']);
  });
});
