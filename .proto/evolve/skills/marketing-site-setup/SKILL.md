---
name: marketing-site-setup
description: Scaffold and plan a new marketing site — branding audit, stack selection, hero design, and deployment config
---

# /marketing-site-setup

## When to use
Use when starting a new product marketing site that needs to inherit an existing brand system, feature an interactive hero, and be deployed to a specific subdomain via Cloudflare Pages.

## Steps
1. **Audit existing branding repo** — Pull design tokens, color palette, typography, and component conventions from the reference repo (e.g. `protomaker`)
2. **Define stack** — Choose framework (e.g. Astro + Tailwind + React islands), confirm where the site lives in the monorepo (e.g. `sites/marketing/`)
3. **Design hero section** — Decide on interactive hero component (e.g. canvas-rendered orb), interactivity goals, and demo hook
4. **Lock deployment config** — Confirm subdomain (e.g. `orbis.protolabs.studio`), hosting provider (Cloudflare Pages), and footer/legal copy (e.g. `© 2026 protolabs.studio`)
5. **Define page sections** — Plan the full page: hero, features, demo, CTA, footer
6. **Write copy direction** — Confirm headline tone and tagline before generating content
7. **Scaffold files** — Generate Astro pages, components, and config files

## Examples
- `orbis.protolabs.studio` — Astro 5 + Tailwind v4 + React orb hero, deployed via Cloudflare Pages, inheriting ProtoLabs studio branding
- `protomaker.protolabs.studio` — Same pattern applied to the Protomaker product with its own hero interaction and copy
