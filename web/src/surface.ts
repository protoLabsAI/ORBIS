import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';

/**
 * Experimental "surface" features — the command bar, widgets, and the ambient
 * mini-orb — are behind the `ORBIS_SURFACE` flag while they bake. The Rust side
 * reads the env var and gates its own bits (global hotkey, ambient mode); this
 * hook lets the frontend gate the widget dock / launcher / ambient bridge on the
 * same flag. Off unless ORBIS_SURFACE is 1/true/on.
 */
let cached: boolean | null = null;

export function useSurfaceEnabled(): boolean {
  const [on, setOn] = useState(cached ?? false);
  useEffect(() => {
    if (cached !== null) {
      setOn(cached);
      return;
    }
    let alive = true;
    invoke<boolean>('surface_enabled')
      .then((v) => {
        cached = v;
        if (alive) setOn(v);
      })
      .catch(() => {
        // not in a Tauri context, or command missing — stay disabled
      });
    return () => {
      alive = false;
    };
  }, []);
  return on;
}
