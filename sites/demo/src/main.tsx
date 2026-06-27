import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import App from '@/App';
import { startDemoEngine } from './engine/mockEngine';

// The real ORBIS app expects a dark, data-theme="dark" document (set by
// the Tauri shell's index.html). index.html here mirrors that; belt-and-
// braces in case a host strips the attributes.
document.documentElement.classList.add('dark');
document.documentElement.dataset.theme = 'dark';

// Bring the in-browser "backend" to life: emit the session/voice events
// the app's bridge listens for. PR1 = a scripted mock; PR2 swaps in the
// on-device Gemma/Whisper/Kokoro engine behind the same event surface.
startDemoEngine();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
