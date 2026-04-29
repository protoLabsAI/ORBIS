import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { VitePWA } from 'vite-plugin-pwa';
import glsl from 'vite-plugin-glsl';
import path from 'node:path';

const ORBIS_BACKEND = process.env.ORBIS_BACKEND_URL ?? 'http://localhost:7866';

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // Import `.glsl` / `.vert` / `.frag` as strings with hot-reload.
    // Powers the R3F shader-material pipeline under plugins/orb/variants/.
    glsl({ minify: false, watch: true }),
    // PWA service worker self-destructs on next launch for any user
    // who has the previous PWA installed. The web/PWA target was
    // dropped on 2026-04-28 (DECISIONS.md amendment of that date) —
    // ORBIS is now Apple-Silicon-only via the Tauri shell, where a
    // service worker only causes stale-cache failure modes.
    //
    // Once we're confident no users are on the legacy installable
    // PWA path, this whole VitePWA() call goes away in a follow-up.
    VitePWA({
      selfDestroying: true,
      registerType: 'autoUpdate',
      workbox: {
        navigateFallbackDenylist: [/^\/api\//, /^\/\.well-known\//, /^\/static\//],
        globPatterns: [],
        runtimeCaching: [],
        skipWaiting: true,
        clientsClaim: true,
      },
      devOptions: { enabled: false, type: 'module' },
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    // Allow the dev server to be reached via Tailscale serve on :8443.
    allowedHosts: ['protolabs.taild25506.ts.net', 'localhost', '.ts.net'],
    proxy: {
      '/api': { target: ORBIS_BACKEND, changeOrigin: true },
      // Legacy static assets (orb images, etc.) — one-release deprecation window.
      '/static': { target: ORBIS_BACKEND, changeOrigin: true },
      // Well-known A2A agent card.
      '/.well-known': { target: ORBIS_BACKEND, changeOrigin: true },
    },
  },
});
