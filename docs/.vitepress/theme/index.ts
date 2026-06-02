// The shared protoLabs.studio VitePress theme — styled from the
// @protolabsai/design tokens, so the brand stays in one place across every
// studio docs site (no per-repo drift). See protoContent's vitepress-theme.
//
// We extend it with one local override: a studio-attribution footer injected
// into the default theme's `layout-bottom` slot so it follows *every* page,
// including sidebar pages (VitePress's built-in `themeConfig.footer` hides
// itself on sidebar layouts at desktop widths).
import { h } from 'vue';
import type { Theme } from 'vitepress';
import DefaultTheme from 'vitepress/theme';
import theme from '@protolabsai/vitepress-theme';
import StudioFooter from './StudioFooter.vue';

export default {
  ...theme,
  Layout() {
    return h(DefaultTheme.Layout, null, {
      'layout-bottom': () => h(StudioFooter),
    });
  },
} satisfies Theme;
