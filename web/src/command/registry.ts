import type { ComponentType } from 'react';

/**
 * Command-bar action registry. Actions come from *providers* (functions that
 * return the current actions) so they can reflect live state — e.g. a widget
 * shows "Open" or "Close" depending on whether it's docked right now.
 *
 * This is the unifying surface the user asked for: widgets register actions
 * here today; delegates (via the adapter registry), tools, and skills register
 * here as those land — so the command bar always offers the same capabilities
 * the agent has. Keyboard-fast complement to voice.
 */

export interface CommandAction {
  id: string;
  title: string;
  subtitle?: string;
  icon?: ComponentType<{ className?: string }>;
  keywords?: string[];
  run: () => void | Promise<void>;
}

type Provider = () => CommandAction[];

const providers = new Set<Provider>();

export function registerActions(provider: Provider): void {
  providers.add(provider);
}

/** Collect the live action list — called each time the bar opens / filters. */
export function collectActions(): CommandAction[] {
  const out: CommandAction[] = [];
  for (const p of providers) {
    try {
      out.push(...p());
    } catch {
      // a misbehaving provider shouldn't take down the palette
    }
  }
  return out;
}
