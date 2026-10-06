import { describe, expect, test } from 'bun:test';
import { DEFAULT_ACTIVATION, persistActivation, wakeReady } from '../src/shared/wakeword/activation';
import type { WakeModel } from '../src/lib/api';

function model(id: string, kind: WakeModel['kind'], downloaded: boolean): WakeModel {
  return { id, kind, downloaded, name: id, description: '', filename: '', url: '', size_kb: 1, recommended: false, license: 'Apache-2.0', source_url: '' };
}
const shared = [model('melspectrogram', 'shared', true), model('embedding', 'shared', true)];

describe('wake-word activation', () => {
  test('remains opt-in and uses a standard fresh phrase choice', () => {
    expect(DEFAULT_ACTIVATION.style).toBe('push_to_talk');
    expect(DEFAULT_ACTIVATION.model).toBe('hey_jarvis');
  });
  test('a different downloaded phrase cannot unlock a missing selected model', () => {
    expect(wakeReady([...shared, model('hey_jarvis', 'wake', true)], 'hey_orbis')).toBeFalse();
    expect(wakeReady([...shared, model('hey_jarvis', 'wake', true)], 'hey_jarvis')).toBeTrue();
  });
  test('both shared models and a verified selected phrase are required', () => {
    const phrase = model('hey_jarvis', 'wake', true);
    expect(wakeReady([phrase], phrase.id)).toBeFalse();
    expect(wakeReady([shared[0], phrase], phrase.id)).toBeFalse();
    expect(wakeReady([shared[0], model('embedding', 'shared', false), phrase], phrase.id)).toBeFalse();
    expect(wakeReady([...shared, model(phrase.id, 'wake', false)], phrase.id)).toBeFalse();
    expect(wakeReady([...shared, phrase], '')).toBeFalse();
  });
  test('native rejection cannot return a committed UI configuration', async () => {
    const next = { ...DEFAULT_ACTIVATION, style: 'wake_word' as const };
    const before = { ...DEFAULT_ACTIVATION };
    await expect(persistActivation(next, async () => { throw new Error('Download the selected wake word'); })).rejects.toThrow('Download');
    expect(before.style).toBe('push_to_talk');
  });
  test('persistence preserves selection, sensitivity and listen window', async () => {
    const next = { ...DEFAULT_ACTIVATION, style: 'wake_word' as const, model: 'hey_orbis', threshold: 0.7, listen_window_s: 8 };
    const writes: unknown[] = [];
    const committed = await persistActivation(next, async (command, args) => { writes.push({ command, args }); });
    expect(committed).toEqual(next);
    expect(writes).toEqual([{ command: 'set_activation_config', args: { style: 'wake_word', model: 'hey_orbis', threshold: 0.7, listenWindowS: 8 } }]);
  });
});
