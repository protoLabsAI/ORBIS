/**
 * Resizable + collapsible side panel — the collapse/reopen model from
 * protoContent-ds `AppShell` (ADR 0035 dual-rail shell), distilled to this
 * editor's single right panel.
 *
 * The panel is pinned to the RIGHT edge, so it grows as the pointer moves left:
 * width = startWidth + (startX − pointerX). The gesture runs on WINDOW pointer
 * listeners so it survives the layout change when the panel closes.
 *
 * Behavior (the "nice" part):
 *  - Drag the divider IN past half the min width and release → the panel
 *    collapses. Collapse is only COMMITTED on pointer-up, so dragging back out
 *    before releasing recovers an accidental close. During the drag the panel
 *    may shrink past its min toward the edge (it doesn't fight you).
 *  - Collapsed → a slim reopen rail you can DRAG back open (it sizes under the
 *    pointer) or just CLICK / press Enter to pop back to its last width.
 *  - Double-click the divider (or `reset`) snaps to the default width; ←/→
 *    nudge it. Width + collapsed state persist to localStorage.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';

const STORAGE_KEY = 'orbis-editor.panelWidth.v1';
const COLLAPSE_KEY = 'orbis-editor.panelCollapsed.v1';

export const DEFAULT_PANEL_WIDTH = 680;
const MIN_PANEL_WIDTH = 380;
/** Always leave the preview at least this much room — the orb never vanishes. */
const MIN_PREVIEW_WIDTH = 420;
/** Drag the panel narrower than this and release → collapse (DS: half the min). */
const COLLAPSE_AT = Math.round(MIN_PANEL_WIDTH * 0.5);
/** Pointer travel (px) under which a reopen gesture counts as a click, not a drag. */
const CLICK_SLOP = 3;

function maxPanelWidth(): number {
  return Math.max(MIN_PANEL_WIDTH, window.innerWidth - MIN_PREVIEW_WIDTH);
}

/** Width snapped into the valid OPEN range [min, max] — for rest / reopen / reset. */
function clampOpen(w: number): number {
  return Math.round(Math.min(maxPanelWidth(), Math.max(MIN_PANEL_WIDTH, w)));
}

/** Width allowed DURING a drag: 0..max, so the panel can slide past its min
 *  toward the edge (the collapse gesture needs to reach below min). */
function clampDrag(w: number): number {
  return Math.round(Math.max(0, Math.min(maxPanelWidth(), w)));
}

function loadWidth(): number {
  try {
    const saved = Number(localStorage.getItem(STORAGE_KEY));
    if (Number.isFinite(saved) && saved > 0) return clampOpen(saved);
  } catch {
    /* hardened browser context — fall back to the default */
  }
  return DEFAULT_PANEL_WIDTH;
}

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1';
  } catch {
    /* hardened browser context — default to expanded */
  }
  return false;
}

export interface ResizablePanel {
  width: number;
  dragging: boolean;
  collapsed: boolean;
  min: number;
  max: number;
  /** Explicit collapse (e.g. a chevron in the panel header). */
  collapse: () => void;
  /** Explicit open to the last width (rail click / Enter). */
  expand: () => void;
  /** Snap to the default width (double-click the divider). */
  reset: () => void;
  /** Spread onto the divider separator shown while OPEN. */
  dividerProps: {
    onPointerDown: (e: ReactPointerEvent) => void;
    onKeyDown: (e: ReactKeyboardEvent) => void;
    onDoubleClick: () => void;
  };
  /** Spread onto the reopen rail shown while COLLAPSED. */
  reopenProps: {
    onPointerDown: (e: ReactPointerEvent) => void;
    onKeyDown: (e: ReactKeyboardEvent) => void;
  };
}

export function useResizablePanel(): ResizablePanel {
  const [width, setWidth] = useState(loadWidth);
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const [dragging, setDragging] = useState(false);
  const gesture = useRef<{ move: (e: PointerEvent) => void; up: (e: PointerEvent) => void } | null>(null);

  // Persist width, debounced — a drag fires setWidth ~per frame; one write at
  // rest is enough (mirrors the store's draft-save cadence).
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

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
    } catch {
      /* best-effort */
    }
  }, [collapsed]);

  // Re-clamp when the window shrinks under a wide panel.
  useEffect(() => {
    const onResize = () => setWidth((w) => clampOpen(w));
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

  const attach = useCallback(
    (move: (e: PointerEvent) => void, up: (e: PointerEvent) => void) => {
      gesture.current = { move, up };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
      setDragging(true);
    },
    [],
  );

  // Divider drag (panel open): resize, and collapse if dragged in past the
  // threshold — committed on release so you can back out.
  const onDividerPointerDown = useCallback(
    (e: ReactPointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = width;
      const widthAt = (clientX: number) => clampDrag(startW + (startX - clientX));
      const move = (ev: PointerEvent) => setWidth(widthAt(ev.clientX));
      const up = (ev: PointerEvent) => {
        const raw = widthAt(ev.clientX);
        if (raw < COLLAPSE_AT) {
          setCollapsed(true);
          setWidth(clampOpen(startW)); // sane width for next open
        } else {
          setWidth(clampOpen(raw));
        }
        endGesture();
      };
      attach(move, up);
    },
    [width, attach, endGesture],
  );

  // Reopen rail (panel collapsed): a click pops it back to its last width; a
  // drag reveals it and sizes it under the pointer (re-collapses if released
  // before clearing the threshold).
  const onReopenPointerDown = useCallback(
    (e: ReactPointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      let revealed = false;
      const move = (ev: PointerEvent) => {
        if (!revealed && Math.abs(ev.clientX - startX) > CLICK_SLOP) {
          revealed = true;
          setCollapsed(false);
        }
        if (revealed) setWidth(clampDrag(startX - ev.clientX));
      };
      const up = (ev: PointerEvent) => {
        if (!revealed) {
          // Click, not drag → pop open to the remembered width.
          setCollapsed(false);
          setWidth((w) => clampOpen(w || DEFAULT_PANEL_WIDTH));
        } else {
          const raw = clampDrag(startX - ev.clientX);
          if (raw < COLLAPSE_AT) setCollapsed(true);
          else {
            setCollapsed(false);
            setWidth(clampOpen(raw));
          }
        }
        endGesture();
      };
      attach(move, up);
    },
    [attach, endGesture],
  );

  const onDividerKeyDown = useCallback((e: ReactKeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const step = e.shiftKey ? 48 : 16;
    // ArrowLeft grows the panel (matches the drag direction); ArrowRight shrinks it.
    setWidth((w) => clampOpen(w + (e.key === 'ArrowLeft' ? step : -step)));
  }, []);

  const collapse = useCallback(() => setCollapsed(true), []);
  const expand = useCallback(() => {
    setCollapsed(false);
    setWidth((w) => clampOpen(w || DEFAULT_PANEL_WIDTH));
  }, []);
  const reset = useCallback(() => {
    setCollapsed(false);
    setWidth(clampOpen(DEFAULT_PANEL_WIDTH));
  }, []);

  const onReopenKeyDown = useCallback(
    (e: ReactKeyboardEvent) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      expand();
    },
    [expand],
  );

  return {
    width,
    dragging,
    collapsed,
    min: MIN_PANEL_WIDTH,
    max: maxPanelWidth(),
    collapse,
    expand,
    reset,
    dividerProps: { onPointerDown: onDividerPointerDown, onKeyDown: onDividerKeyDown, onDoubleClick: reset },
    reopenProps: { onPointerDown: onReopenPointerDown, onKeyDown: onReopenKeyDown },
  };
}
