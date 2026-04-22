import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { VitePWA } from 'vite-plugin-pwa';
import glsl from 'vite-plugin-glsl';
import path from 'node:path';

const PROTOVOICE = process.env.PROTOVOICE_URL ?? 'http://localhost:7866';

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
        name: 'protoVoice',
        short_name: 'protoVoice',
        description: 'Duplex voice agent with fractal orb visualizer',
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
      '/api': { target: PROTOVOICE, changeOrigin: true },
      // Legacy static assets (orb images, etc.) — one-release deprecation window.
      '/static': { target: PROTOVOICE, changeOrigin: true },
      // Well-known A2A agent card.
      '/.well-known': { target: PROTOVOICE, changeOrigin: true },
    },
  },
});
