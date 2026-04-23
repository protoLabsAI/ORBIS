# HANDOFF — ORBIS

*Prepared 2026-04-23 after completing tasks #45-67 (all 23).*

This doc is for the next human to sit down with ORBIS — whether
that's tomorrow-you, a teammate picking it up, or a handoff to a
contracted team. It covers: current state, a concrete QA checklist
to run before declaring anything "working," known open questions,
and ordered next steps.

[STATUS.md](./STATUS.md) has the point-in-time snapshot.
[DECISIONS.md](./DECISIONS.md) has the frozen architecture decisions
— read that first if you haven't. [README.md](./README.md) has the
developer-facing overview.

## Context at a glance

- **Provenance:** Forked from
  [protoLabsAI/protoVoice](https://github.com/protoLabsAI/protoVoice)
  at v0.12.1, then demolished and rebuilt as ORBIS. The carve
  removed the skills catalog, multi-tenant auth, voice cloning, and
  Fish as default TTS. The rebuild added single-persona loading,
  SQLite memory, personality drift, soft-neglect, Stripe
  entitlement, setup wizard, and a bunch of UI panels.
- **Product shape:** Voice-first AI companion. Single-owner. Tailnet
  hostable. Router-first (delegates to the user's configured agents;
  doesn't try to be a framework itself). Differentiator is the
  *companion* layer: memory + personality + mood + soft-neglect.
- **Business model:** Free tier ships a complete product with one
  pre-authored starter orb. Paid tier (one-time Stripe purchase)
  unlocks full customization. No gacha, no loot boxes, no energy
  timers, no pay-to-progress.
- **Status:** Architecture is locked (see DECISIONS.md amendments
  for the few revisions). Engineering spine is complete. Live-boot
  verification is the last gate before shipping a tag.

## What works (verified)

- `python` can import `app.py`, `agent.persona`, `agent.personality`,
  `agent.neglect`, `agent.starter_orbs`, `agent.config_store`,
  `agent.entitlement`, `memory.*` cleanly. No circular imports, no
  missing deps.
- `.venv/bin/python -m pytest` → 104 passing, zero failures. This
  covers:
  - `tests/test_users.py` — single-owner auth primitive
  - `tests/test_memory.py` — sessions, facts (add/search/decay/prune),
    personality axes + mood, entitlement cache expiry
  - `tests/test_persona.py` — YAML parsing, env overrides, malformed
    input fallback, reload semantics
  - `tests/test_personality_render.py` — axis label math + rendering
  - `tests/test_neglect.py` — day-bucket → mood target mapping
  - `tests/test_starter_orbs.py` — pool loader + shipped YAML
    structure
  - `tests/test_config_store.py` — read/write/validate/merge_patch
  - `tests/test_entitlement.py` — configuration gating, webhook
    grant/revoke, dev-mode open-by-default
- Persona loads from `config/orbis.example.yaml` and defaults
  kick in when the file is absent.
- `POST /api/config` validates + writes + reloads persona (tested
  via round-trips against a tempfile).

## What's probable but unverified (needs QA)

These are things I believe work based on code review + unit tests
but have not been validated against a live system. **Run through this
list before committing ORBIS to production or shipping externally.**

### QA checklist

#### Backend

- [ ] `python app.py` boots cleanly on a fresh checkout after
  `pip install -e .` (the current `pyproject.toml` deps install
  without errors)
- [ ] `/healthz` returns 200 + the expected shape
- [ ] `/api/whoami` resolves to `default` in single-user fallback
- [ ] `/api/starter_orbs` returns the 8 shipped entries
- [ ] `/api/personality` returns the seeded default axes (all 0.0)
  after first call (the seed happens on `get_memory()` first access)
- [ ] `/api/config` GET on an empty install returns `{"config": {}}`
- [ ] `/api/config` POST with a full persona block writes
  `config/orbis.yaml` correctly and `/api/persona/reload` picks up
  the change
- [ ] `/api/config` POST with an invalid `tts_backend` returns HTTP 400
- [ ] `/api/orb/select_starter` with a known slug updates
  `config/orbis.yaml` and an unknown slug returns 404
- [ ] `/api/entitlement` in dev mode (no Stripe env) returns
  `{"customization": {"active": true, "configured": false}}` —
  verify the dev-open behavior is what you want for your pre-launch
  environment (see §"Known open questions" below)
- [ ] `/api/entitlement/checkout` returns 503 without Stripe configured
- [ ] `/api/stripe/webhook` returns 503 without Stripe configured
- [ ] Real Stripe webhook (test mode) actually verifies signatures +
  grants entitlement

#### Voice pipeline (end-to-end)

- [ ] Actual WebRTC session connects and stays up for >30 seconds
- [ ] Kokoro TTS synthesizes on first turn (cold-compile may add
  ~10s latency the very first time)
- [ ] User voice → STT → LLM → TTS → audio out works round-trip
- [ ] Session end writes a row to `data/orbis.sqlite` (verify with
  `sqlite3 data/orbis.sqlite "SELECT session_id, started_at FROM sessions;"`)
- [ ] Second session opens with the `<prior_sessions>` block in the
  system prompt (add a `logger.debug` temporarily or inspect the
  LLM call via Langfuse)
- [ ] `delegate_to` actually dispatches to a configured delegate
  (requires at least one `config/delegates.yaml` entry with real
  credentials)
- [ ] Orb-control tools fire correctly — say "be warmer" and watch
  the pipeline log emit `[orb] apply_palette → ...`
- [ ] Paid-tier gate engages when Stripe is configured + no
  entitlement — orb-control tools respond with "That change is
  part of the customization unlock."
- [ ] Personality drift analyzer runs after session end (look for
  `[personality] applied N drift delta(s)` or errors)

#### Frontend

- [ ] `cd web && bun install && bun run build` completes without
  type errors
- [ ] `bun run dev` serves on :5173 and proxies `/api/*` to the
  backend
- [ ] First-run setup wizard appears (clear
  `localStorage['orbis.setupComplete']` to force)
- [ ] Wizard step indicator, Back/Continue buttons, and the
  starter-orb grid all render correctly
- [ ] API-key paste in Access panel hits `/api/whoami` and shows
  "Authenticated" on success
- [ ] API-key paste for a wrong key shows "Key rejected"
- [ ] Profile panel shows axes + mood + session stats after a few
  sessions have accumulated
- [ ] Hatch animation plays full length without visual artifacts
  across browsers (tested: Chrome, Safari, Firefox)
- [ ] Orb variants (Fractal / Nebula / Crystal / Particles) all
  render correctly at 60fps on a mid-range laptop
- [ ] Mobile: drawer opens full-screen with orb preview in the top
  half

#### Packaging

- [ ] `docker build -t orbis:local .` succeeds end-to-end after the
  deps trim (vllm + ddgs removed)
- [ ] `docker compose up` with default env boots without Fish
  sidecar (kokoro runs in-process)
- [ ] `docker compose --profile fish up` starts the Fish sidecar
  and the main container can reach it
- [ ] First release tag (`v0.1.0`) triggers the release.yml
  workflow cleanly (images push to `ghcr.io/protolabsai/orbis`)
- [ ] GH PAT is set in the repo secrets so `prepare-release.yml`
  can fire (this was broken on protoVoice; unclear if the repo
  secret was copied over)

## Known issues / rough edges

- **`_active_skill()` naming** — in `app.py` and `a2a/server.py`
  there are still references to `skill_slug_provider` and
  `_active_skill()`. These are a compatibility shim returning the
  Persona. Functional but confusing; due for a rename pass.
- **`agent/session_store.py`** — still in use for orphan-delivery
  stashing, but session summaries are now in SQLite. The file-based
  text summary is redundant; could be retired once we verify the
  SQLite prior-N block is doing the job.
- **No per-variant mood subscription yet** — `moodStore` polls +
  emits but no orb variant subscribes to it. Mood flows into
  prompts (via `render_personality_block`) but doesn't visually
  show in the orb. Biggest gap between what's designed and what's
  implemented.
- **No live-boot smoke test** — everything compiles + tests pass,
  but nobody has actually opened the app and talked to the orb
  end-to-end since the carve. First real boot will likely surface
  small integration gaps.
- **Stripe phone-home** — the refresh loop queries all 10 recent
  checkout sessions globally, not the owner's specifically. Fine
  for single-owner today; needs owner-scoping if ORBIS ever supports
  multi-tenant installs.
- **Drift analyzer silence-on-error** — if the LLM endpoint is
  broken, personality drift silently never happens. Worth a metrics
  counter or a periodic health check so operators know.
- **Frontend CI** — no `bun run build` in the release pipeline.
  `release.yml` copies the built dist in via Docker but never
  validates the TS types in CI. Add a `frontend-check.yml` workflow.
- **No `docs/` site** — the VitePress build was deleted during the
  demolition. The README + DECISIONS + STATUS + HANDOFF do most of
  the work, but users coming from a hosted docs site have nothing.

## Known open questions

These are product/design calls the next session can address:

1. **Dev-mode customization default — open or closed?** Currently
   `has_customization()` returns `true` when Stripe is unconfigured
   so local dev isn't gated. If ORBIS ships as a downloadable app,
   this might be backwards — the dev-friendly default makes testing
   easy but means un-paid production installs also get full access
   unless Stripe is set up. Resolution: decide whether the default
   should flip based on an env var (`ORBIS_GATE=open|closed`) or
   stay dev-open and document that prod installs MUST set Stripe.

2. **Per-variant mood mappings — who authors them?** Today each of
   the four variants has palettes + params but no wiring that
   translates mood into uniform changes. Options:
   - Author them in code per variant (quick, limiting)
   - Ship them as JSON alongside each variant's `presets.ts` (more
     flexible)
   - Build the authoring editor DECISIONS.md envisioned (user
     drags a slider labeled "valence", sees the orb react, saves
     the mapping). Biggest lift, best product outcome.

3. **Starter orb pool curation** — 8 starters ships by default. Is
   that the right number? Too few = not enough personality-match;
   too many = decision paralysis at first boot. Worth a usability
   test on first-run.

4. **Hatch animation** — the current 3.6s CSS reveal is intentionally
   minimum-viable. A richer per-variant shader-driven hatch is a
   follow-up. Question: does the current reveal carry enough
   emotional weight on first boot, or does it read as "a loading
   screen"? A/B or user test.

5. **Stripe price modeling** — the entitlement is modeled as a
   one-time payment (`mode="payment"` in the checkout session). If
   the business model becomes subscription-based (monthly
   customization access), the event-to-entitlement mapping needs
   to add `invoice.paid` + `customer.subscription.*` handling. The
   code scaffolding is there; the grant events list is where you'd
   edit.

6. **Docs site** — rebuild VitePress or something simpler (mkdocs,
   GitHub-flavored wiki)? Until a real user-facing audience exists,
   README + HANDOFF + DECISIONS in-repo are probably enough.

7. **`session_store.py` text summaries** — redundant now that
   SQLite has sessions. Keep as a legacy fallback, or retire
   entirely? Recommend retire in a follow-up task once a week of
   live use confirms SQLite recall is doing the job.

8. **A2A outbound path** — the A2A client is untouched from the
   seed and still works. Not clear whether users will actually use
   it vs just using OpenAI-compat delegates. Usage data after a
   few real installs would inform whether to polish it or deprecate.

## Recommended next steps (in priority order)

### Immediate (before first external demo)

1. **Run the QA checklist above.** Budget ~4 hours. Anything that
   doesn't pass gets a bug-ticket and goes to step 2.
2. **Fix whatever the QA surfaces.** Likely 2-3 integration gaps;
   nothing architectural expected.
3. **Cut v0.1.0.** Tag the release, confirm the Docker image
   publishes, fix any release-pipeline issues.
4. **First real voice session** — record yourself talking to the
   orb for 10 minutes, look at the resulting SQLite state
   (sessions row, any drift events), confirm the prior_n block
   lands in the system prompt on a second session.

### Short-term (next 1-2 weeks)

5. **Per-variant mood wiring.** Pick one variant (suggest Fractal
   since it's the default) and wire `useMood()` into its shader
   uniforms. Prove the mood → visual feedback loop works, then
   replicate for the other three.
6. **`_active_skill()` rename pass.** Cosmetic but reduces
   confusion for anyone reading the codebase cold.
7. **Frontend CI.** Add `.github/workflows/frontend-check.yml` that
   runs `bun install + bun run build` on every PR.
8. **Retire `session_store.py` text summaries.** Remove the
   `save_summary` / `load_last_summary` calls from app.py; keep the
   orphan-delivery functions. One commit's worth of work.

### Medium-term (next month)

9. **State/mood authoring editor.** The big UI task — drag a
   slider, see the orb react, save the delta to the preset. This
   is what unlocks user-generated orbs at scale.
10. **Docs site.** Author a handful of guides: setup, persona
    config, delegate config, state/mood editor, paid unlock flow.
11. **Live Stripe integration test.** Use Stripe test mode end-to-end
    — real Checkout Session, real webhook, real entitlement grant.
12. **Multi-device UX.** Tailnet hosting implies the user talks to
    the orb from laptop and phone, potentially at different times.
    Confirm session handoff works cleanly across devices.

### Longer-term (beyond)

- **Collectible / shop orbs** (per DECISIONS.md: deferred, not
  dropped entirely). Time-limited starter additions, themed drops.
- **Fact extraction background agent.** DECISIONS.md flagged this
  as optional — the SQLite facts table is ready for it, the
  extractor module just needs writing.
- **Multi-tenant hosting** (if product direction shifts). The
  auth primitive could be re-expanded back out to multi-user; the
  skills catalog would need to come back in some form.
- **Observability — Langfuse + Prometheus /metrics endpoint.**
  Stubs exist from the seed; wire to the deployment env.

## Useful commands

```bash
# Inspect memory state (pretty-print sessions + facts)
sqlite3 data/orbis.sqlite \
  "SELECT session_id, ended_at, length(messages) AS msg_chars FROM sessions;"
sqlite3 data/orbis.sqlite \
  "SELECT axis, value, updated_at FROM personality_axes ORDER BY value DESC;"
sqlite3 data/orbis.sqlite \
  "SELECT subject, relation, object, confidence FROM facts WHERE invalid_at IS NULL;"

# Force re-run of setup wizard
# in browser devtools:
localStorage.removeItem('orbis.setupComplete')
localStorage.removeItem('orbis.apiKey')
location.reload()

# Clear memory (full reset)
rm data/orbis.sqlite*

# Trigger personality drift manually (for testing the prompt block)
python -c "
from memory import Memory
m = Memory()
m.personality.seed_defaults()
m.personality.drift('playful_serious', 0.5, 'manual test')
m.personality.set_mood(valence=0.4, arousal=-0.2)
print(m.personality.get_mood())
"

# Test config round-trip
curl -s localhost:7866/api/config | jq
curl -s -X POST localhost:7866/api/config \
  -H "Content-Type: application/json" \
  -d '{"persona":{"name":"Atlas"}}' | jq
```

## Contact

If you're picking this up cold, questions that can't be answered
from DECISIONS.md + STATUS.md + README.md + this file are worth
asking whoever last committed — `git log --format='%an %ae' | sort -u`
is the starting point. The commit messages are deliberately detailed;
most integration decisions are documented in the commit that made
them.

Good luck. The spine is solid — the product is yours to ship.
