# ORBIS Marketing Site — Design Spec

**Date:** 2026-04-26  
**Status:** Approved  
**Hosted at:** `orbis.protolabs.studio` (Cloudflare Pages)  
**Location in repo:** `sites/marketing/`

---

## 1. Overview

A static marketing site for the ORBIS desktop app. Its primary job is to communicate the product's value clearly, let visitors interact with the orb, and get them to download the app or explore further. Four pages at launch: landing, download, docs (getting started), changelog.

---

## 2. Stack

| Layer | Choice | Rationale |
|---|---|---|
| Framework | Astro 5 (static output) | Matches protolabs.studio; SSR-skipped for orb island |
| Styling | Tailwind CSS v4 (CSS-first `@theme {}`) | Matches protolabs.studio exactly |
| Orb rendering | React 19 island (`client:only="react"`) | R3F + Three.js r184 require React; island-only hydration keeps the rest of the page static |
| GLSL | `vite-plugin-glsl` | Already used in `web/` — same integration |
| Deployment | Cloudflare Pages | Same as protolabs.studio; `wrangler.toml` in `sites/marketing/` |
| Analytics | Umami (same instance as studio, new site ID) | |
| Fonts | Geist + Geist Mono via Google Fonts CDN | |

**Build command:** `astro build`  
**Output dir:** `dist/`  
**Node version:** 22 (matches ORBIS root)

---

## 3. Branding

ORBIS uses the ProtoLabs base design language with a distinct accent palette derived from the Aurora orb preset.

### Colors

```css
/* Inherit from protolabs.studio conventions */
--color-surface-0: #09090b;   /* page bg */
--color-surface-1: #111113;
--color-surface-2: #18181b;
--color-surface-3: #222225;

/* ORBIS-specific accents (Aurora palette) */
--color-accent:     #0ea5e9;   /* sky-500 — primary interactive / CTA */
--color-accent-dim: #0284c7;   /* sky-600 — hover */
--color-accent-alt: #f472b6;   /* pink-400 — secondary highlight, gradient terminus */

--color-muted: #71717a;        /* zinc-500 */

/* Borders / glows — same as studio */
--border-subtle:       rgba(255,255,255,0.06);
--border-accent-muted: rgba(14,165,233,0.25);   /* sky-tinted */
--glow-accent: 0 0 80px rgba(14,165,233,0.10);
```

Gradient text utility (hero headline words):
```css
background: linear-gradient(135deg, #0ea5e9 0%, #818cf8 50%, #f472b6 100%);
-webkit-background-clip: text;
-webkit-text-fill-color: transparent;
```

Always dark mode — no light mode toggle.

### Typography

| Role | Family |
|---|---|
| Body / UI | Geist, system-ui, sans-serif |
| Code / mono | Geist Mono, ui-monospace |

### Logo / Favicon

- Wordmark: `ORBIS` in white, weight 600, letter-spacing tight
- Favicon: a 64×64 SVG of the orb — a simple radial-gradient circle (sky→pink) as a placeholder until a proper orb screenshot can be baked in
- No CPU chip icon — that's ProtoLabs brand, not ORBIS

---

## 4. Layout Shell

**Nav** — fixed top, `h-14`, `backdrop-blur-xl bg-surface-0/80 border-b border-white/5`
- Left: `ORBIS` wordmark
- Right: `Download` (text link), `Changelog` (text link), GitHub icon link
- Container: `max-w-5xl mx-auto px-6` (consistent with protolabs.studio)

**Footer** — `border-t border-white/5`, `max-w-5xl mx-auto px-6`
- `© 2026 protolabs.studio · GitHub · Changelog`

---

## 5. Pages

### 5.1 Landing (`/`)

#### Hero section

Full-viewport, vertically centered, single-column layout (the orb is the visual — no 2-column split).

- **Orb canvas** — centered, 480×480px on desktop, 320×320px on mobile. The `<OrbHero />` React island renders the Fractal variant with the Aurora palette locked in. No audio stream; `voiceState` defaults to `"idle"`. Pointer drag spins, click triggers bloom. Idle breath always on.
- **Demo state toggles** — four small pill buttons below the orb: `Idle · Listening · Thinking · Speaking`. Clicking cycles the `voiceState` prop so visitors can preview the four animation states.
- **Eyebrow tag** — `text-accent font-mono text-sm uppercase tracking-widest`: `Voice + Personality for your agents`
- **Headline** — `text-4xl md:text-6xl font-bold leading-[1.1] tracking-tight text-white`: `Give your AI a voice.` (accent gradient on `voice`) Then a second line: `Give it a personality.`
- **Subtitle** — `text-lg text-zinc-400`: `ORBIS sits between you and your agents — listening, routing, and responding with a presence that feels native to your Mac.`
- **CTAs** — two buttons, stacked on mobile, inline on desktop:
  - Primary: `Download for Mac` — `bg-accent hover:bg-accent-dim text-white px-6 py-3 rounded-lg font-medium`
  - Secondary: `View on GitHub` — `border border-white/10 hover:border-white/20 text-zinc-300 px-6 py-3 rounded-lg`
- **Platform tag** — `macOS 13+ · Apple Silicon · Free` in `text-zinc-500 text-sm`

#### Features strip

3-column card grid. `border border-white/5 bg-white/[0.02] rounded-xl p-6` per card.

| Icon | Heading | Body |
|---|---|---|
| 🎙 | Voice-native | Wake word → speak → done. No typing, no switching windows. ORBIS hears you. |
| 🔒 | Runs locally | MLX on Apple Silicon. Your conversations stay on your machine. |
| 🔗 | Delegates anywhere | Connect any OpenAI-compatible agent, A2A endpoint, or local model. ORBIS routes the right request to the right brain. |

#### How it works

3-step horizontal flow, connected by a subtle dashed line:

1. **Wake** — Say the wake word. ORBIS opens the pipeline.
2. **Speak** — Ask anything. ORBIS routes to the right agent, model, or tool.
3. **Done** — Hear the response through your Mac's audio. No app-switching.

Eyebrow: `How it works`. Section heading: `From wake word to answer in under two seconds.`

#### CTA banner

`border-t border-b border-white/5`, full-width tinted background (`bg-white/[0.015]`). Centered:
- Heading: `Ready to try it?`
- Button: `Download for Mac` (same primary style)
- Subtext: `macOS 13+ · Apple Silicon for local LLM · Intel supported with cloud models`

---

### 5.2 Download (`/download`)

Simple, focused. No orb (keep it fast to load).

- **Heading**: `Download ORBIS`
- **Version badge**: `v0.1.41` (dynamically read from `data/releases.json` at build time)
- **Primary download button**: links to the latest GitHub Release `.dmg` asset
- **System requirements** — two-column: Minimum / Recommended
  - Minimum: macOS 13 Ventura, 8 GB RAM, Intel or Apple Silicon
  - Recommended: macOS 14+, Apple Silicon M1+, 16 GB RAM (for on-device MLX)
- **Install steps** — numbered list, `Geist Mono` step numbers:
  1. Download the `.dmg`
  2. Open it and drag ORBIS to Applications
  3. Launch ORBIS — allow microphone access when prompted
  4. The setup wizard walks through the rest
- **Note block**: `ORBIS is unsigned on Intel — macOS will show a Gatekeeper warning. Right-click → Open to proceed.` (if applicable per current build CI)
- **Release history link** → `/changelog`

---

### 5.3 Docs / Getting Started (`/docs`)

Minimal inline docs. Not a full reference — links to GitHub for that.

Sections (in order):
1. **Installation** — same steps as download page, brief
2. **First launch** — the setup wizard covers LLM config, mic, wake word, persona
3. **Connecting an LLM** — OpenAI-compatible endpoint config; Ollama; MLX (Apple Silicon only). Code block: example `orbis.yaml` snippet.
4. **Adding a delegate** — what a delegate is, how to add one via config. Code block: `delegates:` YAML example.
5. **Config file location** — `~/Library/Application Support/ORBIS/orbis.yaml`
6. **Further reading** — links to GitHub README, DECISIONS.md, HANDOFF.md in repo

Style: prose with `<code>` inline and fenced code blocks in `Geist Mono`. `border-l-2 border-accent pl-4` for tip/note callouts.

---

### 5.4 Changelog (`/changelog`)

Sourced from `sites/marketing/data/changelog.json`. Each entry:
```json
{
  "version": "v0.1.41",
  "date": "2026-04-26",
  "changes": ["Removed orb visual control tools from LLM surface", "..."]
}
```

Rendered as a vertical timeline list. Each entry:
- Version badge: `bg-surface-2 border border-white/8 text-accent font-mono text-sm px-2 py-0.5 rounded`
- Date: `text-zinc-500 text-sm`
- Bullet list of changes

Bootstrap with entries from v0.1.40 back to v0.1.0 (from git tags + GitHub release notes). Newest first.

---

## 6. Orb Island — `OrbHero.tsx`

A self-contained React component that renders the Fractal orb with no dependency on the broader ORBIS app context.

### What gets copied

From `web/src/plugins/orb/` into `sites/marketing/src/orb/` (a one-time copy; not a symlink — the marketing orb is intentionally static and decoupled):

| Source | Role |
|---|---|
| `variants/fractal/FractalVariant.tsx` + `materials.ts` + `presets.ts` + `shaders/fractal.frag.glsl` | The orb itself |
| `shared/shaders/sphere.vert.glsl` | Shared vertex shader |
| `shared/atmosphere/Atmosphere.tsx` + `material.ts` + `atmosphere.{vert,frag}.glsl` | Halo shell |
| `shared/chromaticAberration.ts` | Post-processing effect |
| `shared/hooks/useIdleBreath.ts` | Gentle idle animation |
| `shared/hooks/usePointerInteraction.ts` | Drag spin + click bloom |
| `shared/hooks/useStateCrossfade.ts` | Smooth state transitions |
| `shared/hooks/useComposedBase.ts` | Param composition (stripped of store deps — see below) |
| `shared/stateSnapshot.ts` `envelope.ts` `constants.ts` `math.ts` `color.ts` `fibonacciSphere.ts` | Utilities |

### What gets stubbed / removed

| Removed dependency | Replacement in marketing orb |
|---|---|
| `orbStore` / `useSyncExternalStore` | Static Aurora preset object passed as props |
| `moodStore` / `simulationStore` | Removed — `useComposedBase` receives a static `{ moodOverrides: {}, stateOverrides: {} }` |
| `useAudioEnvelopes` | Removed — `botEnv` and `userEnv` pinned to `0` |
| `useVoiceStateSelector` from `@/voice/hooks` | `voiceState` prop passed directly from parent |
| `@pipecat-ai/client-react` | Not imported |
| `configDriver` / `/api/config` | Not used |

### `OrbHero` component API

```tsx
interface OrbHeroProps {
  voiceState?: 'idle' | 'listening' | 'thinking' | 'speaking';
  size?: number; // canvas size in px, default 480
}
```

The Astro page passes `voiceState` from a `<script>` that listens for the demo chip button clicks. Since Astro islands can't receive reactive props from outside JS, the component listens on a `CustomEvent` (`orbis:voiceState`) dispatched from the chip buttons' `<script>` tag. A second event (`orbis:playAudio`) triggers the pre-recorded audio playback (see §10).

### Canvas setup

```tsx
<Canvas
  camera={{ fov: 45, near: 0.1, far: 100, position: [0, 0, 13] }}
  dpr={Math.min(window.devicePixelRatio, 1.5)}  // cap at 1.5 for perf
  gl={{ antialias: true, alpha: false }}
  style={{ background: '#000000', borderRadius: '50%' }}
>
```

Rendered inside a `div` with `border-radius: 50%` and a subtle `box-shadow: 0 0 80px rgba(14,165,233,0.15)` outer glow to integrate it into the dark page.

---

## 7. Cloudflare Pages Setup

`sites/marketing/wrangler.toml`:
```toml
name = "orbis-marketing"
pages_build_output_dir = "dist"
compatibility_date = "2026-04-26"
```

Custom domain `orbis.protolabs.studio` added in the Cloudflare Pages project settings (CNAME to `orbis-marketing.pages.dev`).

GitHub Actions workflow `.github/workflows/marketing-deploy.yml`:
- Trigger: push to `main` with changes in `sites/marketing/**`
- Steps: `bun install` → `astro build` → `wrangler pages deploy dist/`
- Uses `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` secrets (same as existing CI)

---

## 8. File Structure

```
sites/marketing/
├── astro.config.mjs
├── wrangler.toml
├── package.json           (astro, @astrojs/react, tailwindcss, three, @react-three/fiber, @react-three/drei, @react-three/postprocessing, postprocessing, vite-plugin-glsl)
├── tsconfig.json
├── public/
│   ├── favicon.svg        (radial-gradient orb placeholder)
│   └── og-orbis.png       (OG image — orb screenshot on dark bg)
├── src/
│   ├── layouts/
│   │   └── BaseLayout.astro
│   ├── components/
│   │   ├── Nav.astro
│   │   ├── Footer.astro
│   │   └── OrbHero.tsx    (React island — client:only="react")
│   ├── orb/               (copied + stripped orb source)
│   │   ├── variants/fractal/
│   │   ├── shared/
│   │   └── OrbStandaloneCanvas.tsx
│   ├── pages/
│   │   ├── index.astro
│   │   ├── download.astro
│   │   ├── docs.astro
│   │   └── changelog.astro
│   └── styles/
│       └── global.css
└── data/
    └── changelog.json
```

---

## 9. Out of Scope (explicitly deferred)

- Pricing / purchase flow (Stripe) — not on marketing site
- Blog / articles
- Nebula, Crystal, Particles variants on the marketing site — only Fractal/Aurora ships
- Live voice demo (mic access in browser) — too much friction for a marketing page
- Light mode
- i18n
