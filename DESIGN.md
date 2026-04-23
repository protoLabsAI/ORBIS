# ORBIS — design brief

> Seeded from protoVoice (v0.12.1). This file is the handoff doc for the
> team picking ORBIS up. The rest of the tree is protoVoice-as-of-shipment —
> a known-good starting point. Strip or keep any part of it that serves
> the orb direction; this doc explains what's worth keeping vs what isn't,
> and where we meant to take the product.

## What ORBIS is

A collectible-orb product. Users build a personal roster of **orbs**
(named visual presets with rarity tiers), unlocking new ones through
achievements, promo codes, or micro-transactions. The orb itself stays
the audio-reactive 3D object from protoVoice; the product wrapper
around it is new.

The voice-agent machinery in this seed is **not the product**. It's
infrastructure that happened to grow a rich orb plugin system, and
you're inheriting that plugin system to save a few weeks. Everything
else — Pipecat, STT, TTS, A2A, skills-as-personas — is incidental to
what ORBIS is trying to be and should be aggressively pared back or
deleted.

## What "orb" means

**An orb is a named viz preset.** `(variant, palette, params)` plus
metadata: `slug`, `display_name`, `rarity`, `description`, optional
`lore`. Purely cosmetic — no persona, no voice, no LLM.

We considered two other framings before settling:

- **Orb = full skill** (persona + voice + viz): overlaps with
  protoVoice's `skills` system, drags in an LLM + TTS, turns the
  product into a voice-agent clone rather than a collectible.
- **Orb = bundle of viz + sound effects / ambient music**: plausible
  middle path, but stretches Phase 1 scope and the audio side is
  unrelated to why the orb system is good prior art.

Keeping orb = viz preset keeps Phase 1 focused on the collection UX
and postpones the audio question.

## Phases

### Phase 1 — viz-only catalog + manual grants (MVP)

- Catalog in `config/orbs/*.yaml`. Each file: `slug`, `display_name`,
  `rarity`, `description`, `variant`, `palette`, `params`. Rarities as
  a fixed enum (e.g. `common | uncommon | rare | epic | legendary`)
  with an explicit ordering.
- Per-user **unlocks** table. Writable state — YAML is no longer
  enough. Start with SQLite via a tiny DAL; the schema is ~10 lines
  and migration to Postgres later is trivial. Columns:
  `user_id`, `orb_slug`, `unlocked_at`, `source` (`admin_grant` /
  `achievement:<id>` / `promo:<code>` / `purchase:<receipt_id>`).
- Auth model: reuse the API-key + role pattern from the seed
  (`auth/users.py`). Keep admin/user split; admins can grant.
- API surface:
  - `GET /api/orbs` — catalog + the caller's `unlocked` set + `active`
  - `POST /api/orbs` — body `{ slug }`; caller must own it
  - `GET /api/whoami` — id, role, `unlocked_orbs: string[]`
  - `POST /api/admin/orbs/grant` — admin-only, body
    `{ user_id, slug, source? }`; writes to the unlocks table
  - `POST /api/admin/orbs/revoke` — admin-only
- UI: repurpose the drawer. A new "Collection" tab shows a gallery
  grouped by rarity, unlocked items highlighted, locked items
  greyed out with a rarity badge. Selecting an unlocked orb pipes
  through `applySkillViz()` (rename — it's not skills anymore,
  something like `applyOrbViz()`).
- No payments, no achievements, no earn-path. Admins manually grant.
  That's the whole MVP.

### Phase 2 — earn paths

Everything that puts unlocks on the table without an admin grant.

- **Achievements** — a small event bus. Events like `session_count`,
  `consecutive_days`, `shared_a_session`, whatever fits the product.
  Map events → orb unlocks in a YAML rules file.
- **Promo codes** — `POST /api/orbs/redeem` body `{ code }`. Codes
  live in an `orb_promo_codes` table with `uses_remaining`,
  `expires_at`, `unlocks: list[slug]`. Admins create them via a CRUD
  endpoint.
- Unlock-attribution stamps `source` on the row so you can do rarity
  analytics later (what's the common earn-path, what's dead, etc).

### Phase 3 — micro-transactions

- Stripe (likely — fewest surprises for this scope) with webhook
  verification. Build a `/api/orbs/checkout` that creates a Stripe
  Checkout Session for one or more orbs, and a `/stripe/webhook` that
  validates the signature, looks up the payment intent, and grants
  unlocks with `source: purchase:<receipt_id>`.
- **Never** grant on the success redirect alone — webhook is the only
  authoritative path. Redirect is a UX nicety and can lie.
- Legal: ToS, refund policy, tax (Stripe Tax handles most of it).
  Needs a legal pass before Phase 3 ships publicly; not a blocker for
  building the plumbing.
- Consider a "founders edition" free-unlock promo for initial users
  as a Phase-3-adjacent marketing move.

## What to keep from the seed

The pieces worth keeping:

- **`web/`** — the whole frontend. React 19 + Vite 6 + shadcn/ui +
  Tailwind 4 + PWA, with the plugin slot registry
  (`plugins/PluginHost.tsx`) and shadcn drawer already wired.
- **`web/src/plugins/orb/`** — the React Three Fiber orb, variant
  registry, broadcast bus, shared driver hooks. This is the crown
  jewel. Documented at `docs/reference/orb-visualizer.md`.
- **`web/src/plugins/orb-settings/`** — will morph into the collection
  view, but the field/panel machinery transfers.
- **`web/src/plugins/orb/storage.ts`** — localStorage for user-custom
  presets; useful shape for the client cache of an orb catalog.
- **`auth/users.py`** — API-key-to-User dependency. The role split
  (`admin` vs `user`) carries over cleanly. The `allowed_skills`
  field becomes `allowed_orbs` (or disappears entirely if you drop
  the idea of pre-gating the catalog).
- **`auth/infisical.py`** — if you keep server-side secrets in
  Infisical, this client is already shaped for it.
- **`scripts/version.py`** + **`.github/workflows/`** — release
  tooling (prepare-release, release, docker-publish, docs) — retarget
  the image name and you're done.
- **`tests/`** — the FastAPI TestClient pattern + pytest setup. 33
  tests in the seed — use them as templates, delete the specific
  ones once their subjects go.
- **`Dockerfile`** stage 1 (the bun web build) — reuse.

## What to delete (hard)

- `a2a/` — A2A protocol (agent-to-agent) — irrelevant
- `voice/` — STT/TTS glue — irrelevant
- `agent/` — filler generator, delivery controller, memory,
  trace session, user_state (the parts about skill_slug /
  verbosity / fillers) — irrelevant
- `skills/` — persona loader — the mechanism is interesting prior
  art for "catalog with inheritance" but the runtime impl is
  overfit to skills; rewrite fresh for orbs
- `app.py` — protoVoice's FastAPI app; gut it. Keep the patterns
  (`require_user` dependency, per-user state, session resolution)
  but strip every voice-pipeline route
- `static/` — legacy vanilla client, pre-React, already deprecated
  in protoVoice
- `Dockerfile.fish`, `docker-compose.yml`'s fish service — TTS server
- Most of `pyproject.toml`'s deps — pipecat, torch, transformers,
  vllm, kokoro, soundfile, soxr, langfuse. ORBIS probably needs
  `fastapi`, `uvicorn`, `pyyaml`, `httpx`, `sqlite` via stdlib, and
  `stripe` once you hit Phase 3. Cuts the container from ~6 GB to
  ~200 MB.
- `docs/` — almost all pages are about voice/Pipecat/skills/delivery.
  Either delete wholesale and start fresh, or keep only
  `docs/reference/orb-visualizer.md` (the one page about the orb
  plugin) and write around it.
- `config/skills/` + `config/delegates.yaml` + `config/SOUL.md` —
  persona catalog, delegate registry. Irrelevant.

## What to think about before writing code

In priority order:

1. **Storage.** SQLite vs JSON-on-disk vs a cloud DB. SQLite is the
   right default — single file, zero ops, survives container restart
   if you mount the file. Revisit when ORBIS needs >1 server process.
2. **Rarity UX.** Rarity is only meaningful if the player feels the
   difference. Common orbs should be visually restrained; legendary
   orbs should be *obviously* more complex. That's a product-design
   call, not an engineering one — decide before you commit to a
   shader budget.
3. **Cross-device unlocks.** API-key-per-user only works if users
   bring their key everywhere. If ORBIS is going on mobile / in a
   webview, you need real auth — passkeys / OAuth / email magic link.
   Don't build Phase 3 on top of API keys.
4. **Moderation + fraud.** Not in Phase 1, but Phase 3 needs a path
   for chargebacks and suspensions. Decide early: does
   `admin_grant`/`admin_revoke` revert purchases, or is that a
   separate `refund` action with its own audit trail?
5. **Catalog versioning.** When you change a legendary orb's params
   after release, existing owners keep the old look or get the new?
   Probably ship "collection shows what was at unlock time," which
   means unlocks store a snapshot of params, not just the slug.
   (Counterpoint: that locks you out of improving visuals.
   Compromise: store the slug, but the catalog YAML has a
   `frozen_after: <date>` — changes before the date update
   existing unlocks; after the date, new version = new slug.)

## Known-good starting point

The seed tree is protoVoice `v0.12.1`, a.k.a. the end of multi-tenant
Phase 1.5. Docker builds cleanly at the time of this commit; `tests/`
passes (33/33); the React frontend bundles. Treat it as a *working*
starting point — not a blank-slate, not a polished framework.

### How to run the seed as-is (to sanity-check before deleting stuff)

```bash
# Frontend only:
cd web && bun install && bun run dev

# Backend requires a local vLLM + Fish server — don't bother unless
# you want to play with the parent product. For ORBIS you're going
# to rip most of the backend out anyway.
```

### Suggested Day 1

1. Delete the hard-delete list above. One commit per directory so
   the diff is reviewable.
2. Strip `pyproject.toml` to the minimum (`fastapi`, `uvicorn`,
   `pyyaml`, `httpx`, `pytest`).
3. Rewrite `app.py` as a 50-line skeleton with just the auth
   dependency + a `GET /api/whoami` + a `GET /api/orbs` that returns
   an empty catalog.
4. Write `config/orbs/common-default.yaml` as the first orb. The
   starter palette.
5. Decide on the storage file (`data/orbs.sqlite`?) and write the
   DAL: `Unlocks` table, `grant(user_id, slug, source)`,
   `list_for(user_id)`.
6. Drawer's "Collection" tab. Start with a flat grid, group by
   rarity later.

## Contact + context

The seed was extracted 2026-04-22 from
`https://github.com/protoLabsAI/protoVoice`. That repo's `STATUS.md`
describes the state at extraction. If you need to understand a
choice in the seed code, `git log` in the parent repo has the
reasoning — this repo's first commit is squashed and has none of
that history.

Questions that fall outside this doc go back to the protoVoice team.
