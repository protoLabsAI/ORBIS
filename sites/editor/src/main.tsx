import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import { App } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Expose the editor's controls as WebMCP tools so AI agents can drive the orb.
// Lazy-loaded so the MCP runtime + SDK stay out of the initial bundle — the orb
// paints first, then the agent tool surface registers. `App` initialises the
// store at import time, so it is ready by the time this resolves.
void import('./webmcp').then((m) => m.registerEditorTools());
