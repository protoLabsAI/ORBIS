import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import glsl from 'vite-plugin-glsl';
import path from 'node:path';

// PWA support removed 2026-04-28: see DECISIONS.md amendment ("Apple
// Silicon (+ iOS planned) only; drop web/PWA"). Even with
// `selfDestroying: true` to clean up legacy SW registrations, the
// transition SW *itself* intercepts /api/* fetches in WKWebView during
// its activate/claim/destroy cycle and silently hangs them — the wizard
// "Saving…" stuck-forever bug we hit while testing Phase 2a. The plugin
// is gone entirely; if a future ORBIS-on-the-web revival happens, it
// can be re-added with a build-time gate.

const ORBIS_BACKEND = process.env.ORBIS_BACKEND_URL ?? 'http://localhost:7866';

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // Import `.glsl` / `.vert` / `.frag` as strings with hot-reload.
    // Powers the R3F shader-material pipeline under plugins/orb/variants/.
    glsl({ minify: false, watch: true }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      // Shared orb runtime, consumed as source (no install). Exact alias
      // to the package index — web never subpath-imports it.
      '@orbis/orb-runtime': path.resolve(
        __dirname,
        '../packages/orb-runtime/src/index.ts',
      ),
    },
    // The orb-runtime sources sit outside web/, so node resolution from
    // those files would miss web/node_modules — pin their peer deps to
    // this app's single copy.
    dedupe: ['react', 'react-dom', 'three', '@react-three/fiber'],
  },
  server: {
    port: 5173,
    fs: {
      // The orb-runtime package lives one level above the vite root.
      allow: ['..'],
    },
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
