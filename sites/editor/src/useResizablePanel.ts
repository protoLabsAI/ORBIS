/**
 * Resizable side panel — the same divider gesture protoAgent's DS AppShell
 * uses (window-level pointer listeners so the drag survives the pointer
 * roaming over the WebGL preview canvas; keyboard nudge + ARIA on the
 * handle), distilled to what this single-panel editor needs.
 *
 * The panel is pinned to the RIGHT edge, so it grows as the pointer moves
 * left: width = startWidth + (startX − pointerX). Double-click the handle
 * (or call `reset`) to snap back to the default width. Width persists to
 * localStorage, debounced like the editor's draft save.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';

const STORAGE_KEY = 'orbis-editor.panelWidth.v1';

export const DEFAULT_PANEL_WIDTH = 680;
const MIN_PANEL_WIDTH = 380;
/** Always leave the preview at least this much room — the orb never vanishes. */
const MIN_PREVIEW_WIDTH = 420;

function maxPanelWidth(): number {
  return Math.max(MIN_PANEL_WIDTH, window.innerWidth - MIN_PREVIEW_WIDTH);
}

function clampWidth(w: number): number {
  return Math.round(Math.min(maxPanelWidth(), Math.max(MIN_PANEL_WIDTH, w)));
}

function loadWidth(): number {
  try {
    const saved = Number(localStorage.getItem(STORAGE_KEY));
    if (Number.isFinite(saved) && saved > 0) return clampWidth(saved);
  } catch {
    /* hardened browser context — fall back to the default */
  }
  return DEFAULT_PANEL_WIDTH;
}

export interface ResizablePanel {
  width: number;
  dragging: boolean;
  min: number;
  max: number;
  reset: () => void;
  /** Spread onto the divider element (give it `role="separator"`). */
  handleProps: {
    onPointerDown: (e: ReactPointerEvent) => void;
    onKeyDown: (e: ReactKeyboardEvent) => void;
    onDoubleClick: () => void;
  };
}

export function useResizablePanel(): ResizablePanel {
  const [width, setWidth] = useState(loadWidth);
  const [dragging, setDragging] = useState(false);
  const gesture = useRef<{ move: (e: PointerEvent) => void; up: () => void } | null>(null);

  // Persist, debounced — a drag fires setWidth ~per frame; one write at rest
  // is enough (mirrors the store's draft-save cadence).
  useEffect(() => {
    const id = setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, String(width));
      } catch {
        /* best-effort */
      }
    }, 300);
    return () => clearTimeout(id);
  }, [width]);

  // Re-clamp when the window shrinks under a wide panel.
  useEffect(() => {
    const onResize = () => setWidth((w) => clampWidth(w));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const endGesture = useCallback(() => {
    const g = gesture.current;
    if (g) {
      window.removeEventListener('pointermove', g.move);
      window.removeEventListener('pointerup', g.up);
      window.removeEventListener('pointercancel', g.up);
    }
    gesture.current = null;
    setDragging(false);
  }, []);

  // Detach any live gesture if the editor unmounts mid-drag.
  useEffect(() => () => endGesture(), [endGesture]);

  const onPointerDown = useCallback(
    (e: ReactPointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = width;
      const move = (ev: PointerEvent) => setWidth(clampWidth(startW + (startX - ev.clientX)));
      const up = () => endGesture();
      gesture.current = { move, up };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
      setDragging(true);
    },
    [width, endGesture],
  );

  const onKeyDown = useCallback((e: ReactKeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const step = e.shiftKey ? 48 : 16;
    // ArrowLeft grows the panel (matches the drag direction); ArrowRight shrinks it.
    setWidth((w) => clampWidth(w + (e.key === 'ArrowLeft' ? step : -step)));
  }, []);

  const reset = useCallback(() => setWidth(clampWidth(DEFAULT_PANEL_WIDTH)), []);

  return {
    width,
    dragging,
    min: MIN_PANEL_WIDTH,
    max: maxPanelWidth(),
    reset,
    handleProps: { onPointerDown, onKeyDown, onDoubleClick: reset },
  };
}
