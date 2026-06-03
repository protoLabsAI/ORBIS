import type { WidgetSurface } from './registry';

/**
 * Open-widget workspace — which widgets are open and on what surface. Persisted
 * to localStorage so the workspace restores on relaunch. useSyncExternalStore
 * shape (stable snapshot reference between mutations).
 */

export interface OpenWidget {
  id: string;
  surface: WidgetSurface;
}

const STORAGE_KEY = 'orbis.widgets.v1';
type Listener = () => void;

function load(): OpenWidget[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return parsed.filter(
        (w): w is OpenWidget =>
          w && typeof w.id === 'string' && (w.surface === 'dock' || w.surface === 'window'),
      );
    }
  } catch {
    // ignore corrupt state — start empty
  }
  return [];
}

class WidgetWorkspace {
  private open: OpenWidget[] = load();
  private listeners = new Set<Listener>();

  getSnapshot = (): OpenWidget[] => this.open;

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };

  isOpen(id: string): boolean {
    return this.open.some((w) => w.id === id);
  }

  openWidget(id: string, surface: WidgetSurface = 'dock'): void {
    if (this.isOpen(id)) {
      this.setSurface(id, surface);
      return;
    }
    this.open = [...this.open, { id, surface }];
    this.commit();
  }

  close(id: string): void {
    if (!this.isOpen(id)) return;
    this.open = this.open.filter((w) => w.id !== id);
    this.commit();
  }

  toggle(id: string, surface: WidgetSurface = 'dock'): void {
    if (this.isOpen(id)) this.close(id);
    else this.openWidget(id, surface);
  }

  setSurface(id: string, surface: WidgetSurface): void {
    let changed = false;
    this.open = this.open.map((w) => {
      if (w.id === id && w.surface !== surface) {
        changed = true;
        return { ...w, surface };
      }
      return w;
    });
    if (changed) this.commit();
  }

  private commit(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.open));
    } catch {
      // non-fatal — persistence is best-effort
    }
    this.listeners.forEach((l) => l());
  }
}

export const widgetWorkspace = new WidgetWorkspace();
