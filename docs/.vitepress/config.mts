import { defineConfig } from 'vitepress';
import { readdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const DOCS_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const titleCase = (s: string) =>
  s.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

// Diátaxis — the four kinds of documentation. Order is the learning arc.
const SECTIONS: [string, string][] = [
  ['tutorials', 'Tutorials'],
  ['how-to', 'How-to'],
  ['reference', 'Reference'],
  ['explanation', 'Explanation'],
];

// Auto-build a section's sidebar from its *.md (README → "Overview", first).
// Drop new pages into the folder; they appear automatically.
function sidebarFor(section: string) {
  const dir = join(DOCS_ROOT, section);
  if (!existsSync(dir)) return [];
  const files = readdirSync(dir).filter((f) => f.endsWith('.md'));
  files.sort((a, b) =>
    a === 'README.md' ? -1 : b === 'README.md' ? 1 : a.localeCompare(b),
  );
  return files.map((f) => ({
    text: f === 'README.md' ? 'Overview' : titleCase(f.replace(/\.md$/, '')),
    link: `/${section}/${f.replace(/\.md$/, '')}`,
  }));
}

export default defineConfig({
  title: 'ORBIS',
  description:
    'ORBIS — a voice-first AI companion for Apple Silicon. User documentation.',
  cleanUrls: true,
  lastUpdated: true,
  // Internal dev/architecture notes live under docs/internal/ and are not
  // part of the published user docs.
  srcExclude: ['internal/**'],

  head: [
    ['meta', { name: 'theme-color', content: '#9b87f2' }],
    ['meta', { property: 'og:title', content: 'ORBIS — docs' }],
    [
      'meta',
      {
        property: 'og:description',
        content: 'A voice-first AI companion for Apple Silicon.',
      },
    ],
  ],

  themeConfig: {
    nav: SECTIONS.map(([s, t]) => ({ text: t, link: `/${s}/README` })),
    sidebar: Object.fromEntries(
      SECTIONS.map(([s, t]) => [`/${s}/`, [{ text: t, items: sidebarFor(s) }]]),
    ),
    search: { provider: 'local' },
    socialLinks: [
      { icon: 'github', link: 'https://github.com/protoLabsAI/ORBIS' },
    ],
    editLink: {
      pattern:
        'https://github.com/protoLabsAI/ORBIS/edit/main/docs/:path',
      text: 'Edit this page on GitHub',
    },
  },
});
