import { defineConfig } from 'astro/config';
import react from '@astrojs/react';
import glsl from 'vite-plugin-glsl';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  output: 'static',
  integrations: [react()],
  vite: {
    plugins: [tailwindcss(), glsl()],
    resolve: {
      alias: {
        '@': '/src',
      },
    },
  },
  site: 'https://orbis.protolabs.studio',
});
