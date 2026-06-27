import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import glsl from 'vite-plugin-glsl';
import path from 'node:path';

// The browser demo — orbis.protolabs.studio/demo. It runs the REAL ORBIS
// frontend (web/src) in a plain browser by aliasing the Tauri API
// entrypoints to a browser shim (src/tauri-shim/*). The app's backend
// (invoke('api_request', …)) is answered locally (src/backend/router.ts)
// and voice events (the orbis-sse stream) are emitted by an in-browser
// engine — a mock in PR1, on-device Gemma/Whisper/Kokoro on WebGPU next.
//
// Built as a standalone SPA and folded into the marketing Cloudflare
// Pages bundle at dist/demo (same posture as /editor and /docs). See
// .github/workflows/marketing-deploy.yml.
//
// Nothing here leaks back into web/src: the app stays Tauri-only (no
// fetch fallback in api.ts) — the browser backend lives entirely in this
// app's module-resolution layer.
const r = (p: string) => path.resolve(__dirname, p);

export default defineConfig({
  base: '/demo/',
  plugins: [
    react(),
    tailwindcss(),
    // The orb variants import .glsl/.frag/.vert as strings.
    glsl({ minify: false }),
  ],
  resolve: {
    alias: {
      // Tauri shim — MUST precede '@' so these specific keys win. (They
      // wouldn't collide anyway: '@' only matches '@/…', not '@tauri-apps/…'.)
      '@tauri-apps/api/core': r('./src/tauri-shim/core.ts'),
      '@tauri-apps/api/event': r('./src/tauri-shim/event.ts'),
      '@tauri-apps/api/window': r('./src/tauri-shim/window.ts'),
      '@tauri-apps/api/path': r('./src/tauri-shim/path.ts'),
      '@tauri-apps/api/app': r('./src/tauri-shim/app.ts'),
      '@tauri-apps/plugin-updater': r('./src/tauri-shim/updater.ts'),
      '@tauri-apps/plugin-process': r('./src/tauri-shim/process.ts'),
      // Consume the real app + shared orb runtime as source.
      '@orbis/orb-runtime': r('../../packages/orb-runtime/src/index.ts'),
      '@': r('../../web/src'),
    },
    // web/src + orb-runtime live outside this root; pin their peer deps
    // to this app's single copy or R3F/three break with duplicate instances.
    dedupe: ['react', 'react-dom', 'three', '@react-three/fiber'],
  },
  server: {
    port: 5175,
    // web/src and packages/* sit above this vite root.
    fs: { allow: ['../..'] },
  },
});
