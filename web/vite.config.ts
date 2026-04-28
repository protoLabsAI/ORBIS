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
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'ORBIS',
        short_name: 'ORBIS',
        description: 'Voice-first AI companion — an orb that talks back, remembers you, and routes to your agents.',
        theme_color: '#0a0a0a',
        background_color: '#0a0a0a',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        icons: [
          { src: '/pwa-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/pwa-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/pwa-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Never intercept the voice pipeline's signalling / media routes.
        // The service worker must stay out of /api/* and /.well-known/*.
        navigateFallbackDenylist: [/^\/api\//, /^\/\.well-known\//, /^\/static\//],
        // Precache the app shell. API responses are never cached.
        globPatterns: ['**/*.{js,css,html,woff2,png,svg}'],
        runtimeCaching: [],
        // Take over immediately on update — critical for the Tauri webview
        // which never closes (a waiting SW would never activate otherwise).
        skipWaiting: true,
        clientsClaim: true,
      },
      devOptions: { enabled: true, type: 'module' },
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
