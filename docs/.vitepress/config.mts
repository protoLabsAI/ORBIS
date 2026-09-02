import { defineConfig } from 'vitepress';
import { readdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const DOCS_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const titleCase = (s: string) =>
  s.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

// Diátaxis — the four kinds of documentation (the top nav). Order is the
// learning arc.
const SECTIONS: [string, string][] = [
  ['tutorials', 'Tutorials'],
  ['how-to', 'How-to'],
  ['reference', 'Reference'],
  ['explanation', 'Explanation'],
];

// Diátaxis > domain (the house pattern — see protoAgent / protoCLI / protoPen):
// the top nav is Diátaxis; INSIDE each section the sidebar is grouped by the
// SAME shared domain taxonomy, so "everything about the orb" sits together
// whether you're reading a how-to, a reference, or an explanation. Order =
// the product's shape.
const DOMAINS: [string, string][] = [
  ['start', 'Getting started'],
  ['voice', 'Voice & conversation'],
  ['orb', 'The orb'],
  ['agents', 'Agents & delegates'],
  ['persona', 'Personalization & memory'],
  ['system', 'Desktop & access'],
];

// Which domain each page belongs to, per section. Key order within a section
// sets the order WITHIN a domain group. A page not listed here still shows up
// (in a "More" group) so a freshly-added `.md` is never lost — then curate it.
const DOMAIN_OF: Record<string, Record<string, string>> = {
  tutorials: {
    'getting-started': 'start',
  },
  'how-to': {
    'train-a-wake-word': 'voice',
    'choose-speech-to-text': 'voice',
    'choose-a-voice': 'voice',
    'manage-reminders': 'voice',
    'voice-not-working': 'voice',
    'customize-your-orb': 'orb',
    'create-custom-orbs': 'orb',
    'author-orbs-with-an-agent': 'orb',
    'configure-the-llm': 'agents',
    'add-a-delegate': 'agents',
    'voice-drive-a-coding-agent': 'agents',
    'personalize-orbis': 'persona',
    'menu-bar-mode': 'system',
  },
  reference: {
    voice: 'voice',
    orb: 'orb',
    'orbis-file-format': 'orb',
    agent: 'agents',
    'memory-and-persona': 'persona',
    config: 'system',
    desktop: 'system',
    access: 'system',
  },
  explanation: {
    'how-orbis-works': 'start',
    'the-voice-loop': 'voice',
    'the-orb': 'orb',
    'the-agent-model': 'agents',
    'agent-to-agent': 'agents',
    'memory-and-persona': 'persona',
    privacy: 'system',
  },
};

// Friendlier sidebar labels than the slug title-case (acronyms, punctuation,
// the `.orbis` format). Falls back to title-case for anything unlisted.
const LABELS: Record<string, string> = {
  'getting-started': 'Getting started',
  'train-a-wake-word': 'Train a wake word',
  'choose-speech-to-text': 'Choose speech-to-text',
  'choose-a-voice': 'Choose a voice (TTS)',
  'manage-reminders': 'Manage reminders',
  'voice-not-working': "Voice isn't working",
  'customize-your-orb': 'Customize your orb',
  'create-custom-orbs': 'Create custom orbs',
  'author-orbs-with-an-agent': 'Author orbs with an agent',
  'configure-the-llm': 'Configure the LLM',
  'add-a-delegate': 'Add a delegate',
  'voice-drive-a-coding-agent': 'Voice-drive a coding agent',
  'personalize-orbis': 'Personalize ORBIS',
  'menu-bar-mode': 'Dock & menu bar',
  'orbis-file-format': 'The .orbis format',
  'memory-and-persona': 'Memory & persona',
  'how-orbis-works': 'How ORBIS works',
  'the-voice-loop': 'The voice loop',
  'the-orb': 'The orb',
  'the-agent-model': 'The agent model',
  'agent-to-agent': 'Agent-to-agent (A2A)',
  access: 'Access & security',
};

const label = (slug: string) => LABELS[slug] ?? titleCase(slug);

// Build a section's sidebar: an Overview link, then one collapsible group per
// domain (in taxonomy order) that has pages here, then a "More" catch-all for
// any page not yet placed in the taxonomy.
function sidebarFor(section: string) {
  const dir = join(DOCS_ROOT, section);
  if (!existsSync(dir)) return [];
  const slugs = readdirSync(dir)
    .filter((f) => f.endsWith('.md') && f !== 'README.md')
    .map((f) => f.replace(/\.md$/, ''));
  const have = new Set(slugs);
  const map = DOMAIN_OF[section] ?? {};

  const sidebar: { text: string; link?: string; collapsed?: boolean; items?: unknown[] }[] = [
    { text: 'Overview', link: `/${section}/README` },
  ];

  for (const [key, groupLabel] of DOMAINS) {
    const items = Object.keys(map)
      .filter((slug) => map[slug] === key && have.has(slug))
      .map((slug) => ({ text: label(slug), link: `/${section}/${slug}` }));
    if (items.length) sidebar.push({ text: groupLabel, collapsed: false, items });
  }

  const placed = new Set(Object.keys(map));
  const more = slugs
    .filter((slug) => !placed.has(slug))
    .sort()
    .map((slug) => ({ text: label(slug), link: `/${section}/${slug}` }));
  if (more.length) sidebar.push({ text: 'More', collapsed: false, items: more });

  return sidebar;
}

// GitHub project Pages serve under /<repo>/; override with DOCS_BASE for a
// custom domain (set DOCS_BASE=/). Local `docs:dev` serves under it too.
const BASE = process.env.DOCS_BASE ?? '/ORBIS/';

export default defineConfig({
  title: 'ORBIS',
  description:
    'ORBIS — a voice-first AI companion for Apple Silicon. User documentation.',
  base: BASE,
  cleanUrls: true,
  lastUpdated: true,
  // Internal dev/architecture notes live under docs/internal/ and are not
  // part of the published user docs.
  srcExclude: ['internal/**'],

  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: `${BASE}favicon.svg` }],
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
    // The ORBIS orb (the product's own mark) in the nav — same gradient as
    // the app icon + favicon. The protoLabs.studio wordmark lives in the
    // footer (see theme/StudioFooter.vue), where the studio attribution goes.
    logo: '/favicon.svg',
    nav: SECTIONS.map(([s, t]) => ({ text: t, link: `/${s}/README` })),
    sidebar: Object.fromEntries(
      SECTIONS.map(([s]) => [`/${s}/`, sidebarFor(s)]),
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
