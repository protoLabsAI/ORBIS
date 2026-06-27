import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import { DemoApp } from './demo/DemoApp';
import { voiceEngine } from './engine/voiceEngine';

// The real ORBIS app expects a dark, data-theme="dark" document (set by
// the Tauri shell's index.html). index.html here mirrors that; belt-and-
// braces in case a host strips the attributes.
document.documentElement.classList.add('dark');
document.documentElement.dataset.theme = 'dark';

// Open a session so the app's bridge flips to connected/idle. Models load
// lazily on first interaction (tap the orb or the mic button).
voiceEngine.init();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <DemoApp />
  </StrictMode>,
);
