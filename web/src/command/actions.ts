import { invoke } from '@tauri-apps/api/core';
import { Mic } from 'lucide-react';
import { type CommandAction, registerActions } from './registry';
import { widgetRegistry } from '../widgets/registry';
import { widgetWorkspace } from '../widgets/store';
import { api } from '../lib/api';
import { toggleVoiceWhenReady } from '../voice/actions';

// Widget open/close — reflects current dock state each time the bar opens.
registerActions(() => {
  const open = new Set(widgetWorkspace.getSnapshot().map((w) => w.id));
  return widgetRegistry.all().map((def): CommandAction => {
    const isOpen = open.has(def.id);
    return {
      id: `widget:${def.id}`,
      title: `${isOpen ? 'Close' : 'Open'} ${def.title}`,
      subtitle: 'Widget',
      icon: def.icon,
      keywords: [def.title, 'widget', isOpen ? 'close hide' : 'open show'],
      run: () => widgetWorkspace.toggle(def.id, def.defaultSurface ?? 'dock'),
    };
  });
});

// Microphone toggle — works from any window via the app commands (native-audio
// builds only; the catch keeps the action harmless elsewhere).
registerActions(() => [
  {
    id: 'mic:toggle',
    title: 'Toggle microphone',
    subtitle: 'Voice',
    icon: Mic,
    keywords: ['mic', 'mute', 'unmute', 'listen'],
    run: async () => {
      try {
        await toggleVoiceWhenReady({
          readLifecycle: async () => (await api.health()).voice?.lifecycle ?? null,
          readListening: () => invoke<boolean>('mic_listening'),
          setListening: (on) => invoke('set_mic_listening', { on }),
        });
      } catch {
        // API/mic commands exist only when the native backend is available.
      }
    },
  },
]);
