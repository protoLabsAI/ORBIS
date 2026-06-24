# Orb gallery — community sharing, voting & curation (plan of record)

Status: **proposed** (decisions locked 2026-06-24). Tracking epic: see the
ORBIS GitHub issues linked at the bottom.

## Goal

Let people **share the orbs they make**, let others **vote**, and let the best
ones **rise to the top** — so the community carries the orb system further.
Curation surfaces: **Featured** and **Orb of the Day**. The flywheel is
*browse → remix in the editor → republish*.

## Locked decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Identity (submit + vote) | **GitHub sign-in (OAuth)** | Real identity → sybil-resistant votes; fits the creative-coding/dev audience. |
| Moderation posture | **Open publish + report/takedown** | Fastest growth; community flags, we remove. Needs a takedown path + name/GPU caps. |
| Access | **Fully free** — browse, vote, submit, *and use shared orbs in-app* | The gallery is top-of-funnel growth; "consume free, create paid" (the editor stays the paid product). |

**Paywall reconciliation (explicit task, not silent):** custom-orb *import* is
currently part of the paid customization unlock. Gallery orbs must import
**free**; arbitrary local `.orbis` import + the **editor** stay gated. The app
distinguishes "gallery-sourced orb" (fetched from our API) from "arbitrary file
import." See Phase 1.

## Why this is a new backend (and why it's cheap)

The site is static/backendless today (Astro + Vite SPA on Cloudflare Pages).
Sharing/voting needs persistence — the **first real backend** for the site. We
reuse the stack the paywall already runs on:

- **Cloudflare Worker** — the gallery API (+ GitHub OAuth, scheduled ranking).
- **D1** (SQLite) — orb metadata, votes, reports, featured.
- **R2** — the `.orbis` files + poster thumbnails (served over the CDN).

**`.orbis` is an ideal payload to host:** pure data, no executable JS, already
**validated + compile-gated**. The Worker re-validates with the dependency-free
`validateOrbDefinition` from `@orbis/orb-runtime` before storing.
(`compileCheck` needs WebGL, so the *compile* gate stays client-side at submit;
the server enforces structure + size caps.)

## Data model (D1)

- **orbs** — `id` (slug), `name`, `description`, `author_gh_id`, `author_gh_login`,
  `created_at`, `orbis_r2_key`, `poster_r2_key`, `tags`, `status`
  (`public|removed`), `votes` (denormalized), `score` (denormalized hot rank),
  `remixed_from` (nullable → lineage).
- **votes** — `orb_id`, `voter_gh_id`, `created_at`; UNIQUE(`orb_id`,`voter_gh_id`)
  → one vote per user per orb.
- **reports** — `orb_id`, `reporter_gh_id`, `reason`, `created_at`, `status`.
- **featured** — `orb_id`, `kind` (`featured|orb_of_the_day`), `date`, `set_by`.
- **users** (cache) — `gh_id`, `gh_login`, `avatar`, `created_at`.

## API (Worker)

- `GET /api/orbs?sort=hot|new|top&page=` — list (poster URL + vote count + did-I-vote).
- `GET /api/orbs/:id` — detail + signed `.orbis` URL.
- `POST /api/orbs` *(auth)* — submit: server-validates the `.orbis`, stores it +
  the poster; rate-limited per user.
- `POST /api/orbs/:id/vote` / `DELETE` *(auth)* — toggle vote (one per user).
- `POST /api/orbs/:id/report` *(auth)* — flag.
- `GET /api/featured`, `GET /api/orb-of-the-day`.
- Admin *(auth + allowlist)* — set featured/OOTD, takedown.
- `GET /auth/github` + callback — OAuth; issues a signed session (reuse the
  paywall's Ed25519/JWT patterns).

## Ranking & curation

- **Hot** score = time-decayed votes (HN/Reddit style, e.g.
  `votes / (age_hours + 2)^gravity`), recomputed by a **Cron Trigger** into the
  denormalized `score`. Sort tabs: **Hot / New / Top**.
- **Orb of the Day** — Cron picks the top orb of the last 24–48h (excluding
  prior winners), or an admin override.
- **Featured** — hand-picked, shown as the gallery hero.

## Frontend

- **`/gallery`** — poster grid + Hot/New/Top tabs + Featured & Orb-of-the-Day
  hero. List is a React island hitting the Worker API; posters are static (no
  live render in the grid → GPU-safe).
- **Orb detail page** — **SSR'd** (Astro on CF Pages Functions) so each orb has
  rich OG/social link previews (the poster) for shareability. Live preview via
  the **same `DefinitionOrb`** the app + editor use. Vote button, **Open in
  editor** (remix), **Use in ORBIS** (free import).
- **Publish from the editor** — an **Export → Publish** action that captures the
  WebGL canvas to a poster PNG and POSTs the `.orbis` (requires GitHub sign-in).
  Reuses the editor's existing import/`loadDefinition` for the remix direction.

## Abuse & safety

- **Vote integrity** — GitHub identity + UNIQUE(orb,voter); submission rate-limit.
- **Content** — open publish + report/takedown; admin takedown; name/description
  length + lightweight profanity filter; `status` flag.
- **GPU safety for viewers** — grid uses static posters; detail/hover live-render
  with a frame/complexity budget; the compile gate means submitted orbs already
  compile; pause render when offscreen; `MAX_FRAGMENT_CHARS` already caps size.
- **Payload** — server `validateOrbDefinition` + size caps before R2 write.

## Phases (→ GitHub sub-issues)

- **P0 — Backend foundation:** Worker + D1 schema + R2 + GitHub OAuth/session.
- **P1 — Publish from editor:** poster capture, `POST /api/orbs`, server
  validation, storage; paywall carve-out for gallery-sourced import.
- **P2 — Browse:** list API + `/gallery` grid + SSR orb detail with OG previews +
  live `DefinitionOrb`.
- **P3 — Voting + ranking:** votes, toggle, hot score + Cron recompute, Hot/New/Top.
- **P4 — Curation + moderation:** Featured, Orb of the Day (Cron + admin),
  report/takedown, admin surface.
- **P5 — Flywheel (stretch):** remix lineage UI, creator profiles, and
  **WebMCP gallery tools** (agent can `publish_orb` from the editor / browse +
  vote) — ties into the WebMCP work (#532) and the `@orbis/orb-mcp` bridge (#536).

## Open questions

- Gallery as Astro routes in `sites/marketing` vs. a dedicated `sites/gallery`
  SPA (lean Astro+islands for SEO on detail pages).
- Exact hot-rank gravity; OOTD auto vs. always-admin to start.
- Creator identity display (GitHub avatar/login) vs. a chosen handle.
- License/attribution on shared orbs (CC-ish? remix credit via `remixed_from`).
