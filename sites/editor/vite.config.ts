import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';

// The orb editor — orbis.protolabs.studio/editor. Built as a standalone
// SPA and folded into the marketing site's Cloudflare Pages bundle at
// dist/editor (same posture as the VitePress docs at /docs). See
// .github/workflows/marketing-deploy.yml + docs/internal/orb-format-and-editor.md.
export default defineConfig({
  base: '/editor/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      // Shared orb runtime, consumed as source — the SAME engine the app
      // renders with, so what you author here is what ORBIS shows.
      '@orbis/orb-runtime': path.resolve(
        __dirname,
        '../../packages/orb-runtime/src/index.ts',
      ),
      // Shared editor panes + store — consumed as source, reused by the in-app editor.
      '@orbis/editor-ui': path.resolve(
        __dirname,
        '../../packages/editor-ui/src/index.ts',
      ),
      // editor-ui's CodeMirror deps live in THIS app's node_modules, but its
      // sources sit outside this root — pin the entry packages so bare imports
      // from packages/editor-ui resolve (their internal deps then resolve here).
      '@uiw/react-codemirror': path.resolve(__dirname, 'node_modules/@uiw/react-codemirror'),
      '@codemirror/lang-cpp': path.resolve(__dirname, 'node_modules/@codemirror/lang-cpp'),
      '@codemirror/lang-json': path.resolve(__dirname, 'node_modules/@codemirror/lang-json'),
    },
    // orb-runtime sources sit outside this root; pin their peer deps to
    // this app's single copy.
    dedupe: ['react', 'react-dom', 'three', '@react-three/fiber'],
  },
  server: {
    port: 5174,
    fs: { allow: ['../..'] },
  },
});
