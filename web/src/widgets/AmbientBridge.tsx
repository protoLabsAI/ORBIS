import { useEffect, useRef } from 'react';
import { listen } from '@tauri-apps/api/event';
import { invoke } from '@tauri-apps/api/core';
import { widgetRegistry } from './registry';
import { widgetWorkspace } from './store';

/**
 * Ambient-presence bridge (main window). When ORBIS's main window is hidden,
 * Rust emits `orbis-main-visibility=false` and shows the mini-orb; we pop the
 * docked widgets out onto the desktop so they stay glanceable. On un-hide we
 * tuck the ones we auto-popped back into the dock (the widget window's own
 * `beforeunload` re-docks it). Manually popped-out widgets are left alone.
 */
export function AmbientBridge() {
  const autoPopped = useRef<Set<string>>(new Set());

  useEffect(() => {
    const unlisten = listen<boolean>('orbis-main-visibility', (e) => {
      const visible = e.payload;
      if (!visible) {
        for (const w of widgetWorkspace.getSnapshot()) {
          if (w.surface !== 'dock') continue;
          autoPopped.current.add(w.id);
          const def = widgetRegistry.get(w.id);
          void invoke('open_widget_window', { id: w.id, title: def?.title ?? w.id })
            .then(() => widgetWorkspace.setSurface(w.id, 'window'))
            .catch(() => {});
        }
      } else {
        for (const id of autoPopped.current) {
          void invoke('close_widget_window', { id }).catch(() => {});
        }
        autoPopped.current.clear();
      }
    });
    return () => {
      void unlisten.then((fn) => fn());
    };
  }, []);

  return null;
}
