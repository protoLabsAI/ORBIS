# HANDOFF — ORBIS

*Updated 2026-04-23 (evening) after the Medium LLM-wizard pass +
first live user test.*

This doc is for the next human to sit down with ORBIS — whether
that's tomorrow-you, a teammate picking it up, or a handoff to a
contracted team. It covers: current state, a QA checklist broken
into verified / still-unverified, known issues, open design
questions, and ordered next steps.

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
  entitlement, setup wizard, orb self-modification tools, and a
  polished LLM-provider setup flow.
- **Product shape:** Voice-first AI companion. Single-owner.
  Tailnet-hostable. Router-first (delegates to the user's
  configured agents; doesn't try to be a framework itself).
  Differentiator is the *companion* layer: memory + personality +
  mood + soft-neglect.
- **Business model:** Free tier ships a complete product with a
  user-picked starter orb. Paid tier (one-time Stripe purchase,
  7-day offline-tolerant cache) unlocks full customization.
- **Status:** Architecture is locked. Engineering spine is complete.
  **Setup wizard + voice round-trip verified live on first user
  test.** Remaining work is polish + follow-ups.

## What works — verified live

- `python -m pytest` → 131 passing, zero failures
- `python app.py` boots cleanly
- Frontend builds + typechecks
- **Setup wizard runs end-to-end** (confirmed 2026-04-23 user test):
  welcome → names → llm → pick → done → hatch, writing
  `config/orbis.yaml` correctly at each step
- **Voice session connects + responds** with a real LLM + TTS
  configured via the wizard
- **Docker container restored** and bootable after the deps carve
- **Starter-orb preview modal** renders the live shader + drag-to-
  rotate works
- **`/api/llm/test` button** successfully validates provider setup
  from the wizard
- **Local auto-detect** surfaces running Ollama / LM Studio when
  present

## What's probable but unverified

These are things I believe work based on code review + unit tests
but have not been validated against a live system. Run through these
before declaring a release.

### QA checklist (still pending)

#### Voice pipeline — beyond the first-hello

- [ ] Multi-turn session (10+ turns) stays stable, no memory leak
- [ ] Session end writes a row to `data/orbis.sqlite` (verify with
  `sqlite3 data/orbis.sqlite "SELECT session_id, ended_at FROM sessions;"`)
- [ ] Second session opens with the `<prior_sessions>` block in the
  system prompt (inspect via Langfuse trace if configured, or add a
  temporary `logger.debug` around `_recall_block`)
- [ ] Soft-neglect kicks in after real days of silence (easiest test:
  hand-edit `ended_at` on a seeded session to `datetime.now - 5 days`
  and observe the orb's mood/nudge on next connect)
- [ ] Personality drift analyzer actually runs + applies deltas
  (look for `[personality] applied N drift delta(s)` in logs or check
  `SELECT * FROM personality_events`)

#### Delegation

- [ ] `delegate_to` with a real A2A target — results stream back
  through the delivery controller + narrate
- [ ] `delegate_to` with an OpenAI-compat target
- [ ] Progress narration during a slow delegation (>5s) kicks in

#### Orb self-modification

- [ ] Say "be warmer" — watch the pipeline log for `apply_palette →`;
  confirm the client-side orb actually reflects the change
- [ ] Say "be more playful" — `adjust_personality` triggers;
  personality_axes table updates
- [ ] Without entitlement configured (dev mode default): variant
  change works (dev mode is open). **With Stripe configured + no
  purchase**: tool returns the "customization unlock" message
  instead of applying

#### Entitlement

- [ ] `/api/config` POST with an `orb` block returns 403 when
  unconfigured + entitlement absent — wait, dev mode is open by
  default so this actually succeeds. To test the gate:
    1. Set `STRIPE_SECRET_KEY + WEBHOOK_SECRET + PRICE_CUSTOMIZATION`
    2. Don't purchase anything
    3. POST `/api/config` with orb block → expect 403
- [ ] Real Stripe test-mode checkout → webhook → entitlement write
- [ ] Cache expiry respects `ENTITLEMENT_CACHE_DAYS` (default 7)

#### Frontend polish

- [x] Setup wizard renders on first boot
- [x] Starter-orb cards render + preview modal works
- [x] Hatch animation timing feels right
- [ ] Profile panel fills in after a few sessions accumulate
- [ ] Voice panel API-key field correctly validates via `/api/whoami`
- [ ] WebGL context-lost warnings (seen during user test) don't
  degrade over a long session

#### Packaging

- [x] Docker image builds (restored post-carve)
- [ ] `docker compose up` boots without Fish sidecar
- [ ] `docker compose --profile fish up` activates Fish
- [ ] First release tag (`v0.1.0`) triggers release.yml cleanly
- [ ] `GH_PAT` set for `prepare-release.yml` auto-bump (was broken
  on protoVoice; status in ORBIS unknown)

## Known issues / rough edges

- **`_active_skill()` naming shim.** `app.py` + `a2a/server.py`
  still reference `skill_slug_provider` / `_active_skill()` —
  compat shims returning the Persona. Functional but confusing; due
  for a rename pass.
- **`agent/session_store.py` text-summary redundant with SQLite.**
  Orphan-delivery stash stays; summary file is now dead code.
  Retire in a focused commit once SQLite recall confirmed working
  over a week of use.
- **No per-variant mood visual mapping.** `moodStore` polls + emits
  but no orb variant subscribes. Mood flows into prompts but doesn't
  visually show in the orb — biggest visible gap vs DECISIONS.md.
- **WebGL context-lost warnings** from user test. Two Canvas
  instances (main orb + preview modal) sometimes collide; the main
  orb's context can get reclaimed. Not fatal — variant re-mounts —
  but worth tightening. Options: pause the main stage while preview
  is open, or share a single Canvas via a portal.
- **Docker hostname resolution UX** (task #68). Wizard accepts bare
  hostnames like `ava` that don't resolve inside containers. Inline
  warning + docs note.
- **Stripe refresh is global, not owner-scoped.** Single-owner today;
  needs scoping if ever multi-tenant.
- **Drift analyzer silence-on-error.** If the LLM endpoint is broken,
  personality drift silently never happens. Worth a metrics counter.
- **Frontend CI.** No `bun run build` in the release pipeline.
  Add `.github/workflows/frontend-check.yml` on next pass.
- **No `docs/` site.** VitePress was purged. README + DECISIONS +
  STATUS + HANDOFF do the work; a rebuilt docs site is future.
- **Select controlled/uncontrolled warning** in console — minor
  shadcn Select component quirk, not blocking.

## Known open questions

1. **Dev-mode customization default — open or closed?** Currently
   open when Stripe is unconfigured so local dev isn't gated. For
   a downloadable install that ships unconfigured, this means
   every un-paid user gets full access. Resolution: env-var toggle
   (`ORBIS_GATE=open|closed`) + document prod installs MUST set
   Stripe before shipping externally.

2. **Per-variant mood mappings — who authors them?** Three options:
   - Code per variant (quick, limiting)
   - JSON alongside each variant's presets (more flexible)
   - Build the authoring editor DECISIONS.md envisioned (biggest
     lift, best product outcome)

3. **Starter orb pool curation.** 8 starters. Right number? Too
   few = not enough personality-match; too many = decision
   paralysis. Worth a usability test.

4. **Stripe price modeling** — currently `mode="payment"`
   (one-time). If the business becomes subscription-based, grant
   events list needs `invoice.paid` + subscription lifecycle
   handling. Code scaffold is there; schema supports it.

5. **Docs site** — rebuild VitePress or something simpler (mkdocs,
   GitHub wiki)? README + HANDOFF + DECISIONS in-repo are probably
   enough until there's a real user-facing audience.

## Recommended next steps (in priority order)

### Immediate (before public-facing demo)

1. **Run the remaining QA checklist items above.** Budget ~2 hours
   for the "unverified" list.
2. **Fix whatever it surfaces.** WebGL context-lost is the most
   likely thing that'll matter at a real demo.
3. **Cut v0.1.0.** Tag the release, confirm the Docker image
   publishes, fix any release-pipeline issues.

### Short-term (next 1-2 weeks)

4. **Per-variant mood wiring.** Pick Fractal (default) and wire
   `useMood()` into its shader uniforms. Prove the mood → visual
   feedback loop works, then replicate for the other three.
5. **`_active_skill()` rename pass.** Cosmetic but reduces
   confusion. Budget 1 hour.
6. **Frontend CI.** Add `.github/workflows/frontend-check.yml` that
   runs `bun install + bun run build` on every PR.
7. **Retire `session_store.py` text summaries.** Remove the
   `save_summary` / `load_last_summary` calls from app.py; keep
   orphan-delivery functions. One commit.
8. **Task #68 — Docker hostname warning** in the LLM wizard step
   when a bare hostname is entered. Inline caveat + docs note.

### Medium-term (next month)

9. **State/mood authoring editor.** The big UI task — drag a
   slider, see the orb react, save the delta to the preset. This
   is what unlocks user-generated orbs at scale.
10. **Docs site.** Author a handful of guides: setup, persona
    config, delegate config, state/mood editor, paid unlock flow.
11. **Live Stripe integration test.** Use Stripe test mode
    end-to-end — real Checkout Session, real webhook, real grant.
12. **Multi-device UX.** Tailnet implies phone + laptop at
    different times. Confirm session handoff is clean.

### Longer-term

- **Collectible / shop orbs** (per DECISIONS.md: deferred). Time-
  limited starter additions, themed drops.
- **Fact extraction background agent.** SQLite facts table is
  ready; extractor module just needs writing.
- **Observability — Langfuse + Prometheus `/metrics`.** Stubs
  exist from the seed; wire to the deployment env.
- **Multi-tenant hosting** (if product direction shifts). Auth
  primitive could re-expand; skills catalog would need a revival.

## Useful commands

```bash
# Inspect memory state
sqlite3 data/orbis.sqlite \
  "SELECT session_id, ended_at, length(messages) AS msg_chars FROM sessions ORDER BY ended_at DESC LIMIT 10;"
sqlite3 data/orbis.sqlite \
  "SELECT axis, value, updated_at FROM personality_axes ORDER BY abs(value) DESC;"
sqlite3 data/orbis.sqlite \
  "SELECT subject, relation, object, confidence FROM facts WHERE invalid_at IS NULL ORDER BY confidence DESC;"
sqlite3 data/orbis.sqlite \
  "SELECT axis, delta, reason, at FROM personality_events ORDER BY at DESC LIMIT 20;"

# Force re-run of setup wizard (browser devtools)
localStorage.removeItem('orbis.setupComplete')
localStorage.removeItem('orbis.apiKey')
location.reload()

# Clear memory (full reset)
rm data/orbis.sqlite*

# Test LLM endpoint without going through the pipeline
curl -X POST http://localhost:7866/api/llm/test \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://api.openai.com/v1","model":"gpt-4o-mini","api_key":"sk-..."}'

# Test config round-trip
curl -s localhost:7866/api/config -H "X-API-Key: $ORBIS_KEY" | jq
curl -s -X POST localhost:7866/api/config \
  -H "X-API-Key: $ORBIS_KEY" \
  -H "Content-Type: application/json" \
  -d '{"persona":{"name":"Atlas"}}' | jq

# Trigger personality drift manually (testing the prompt block)
python -c "
from memory import Memory
m = Memory()
m.personality.seed_defaults()
m.personality.drift('playful_serious', 0.5, 'manual test')
m.personality.set_mood(valence=0.4, arousal=-0.2)
print(m.personality.get_mood())
"
```

## Contact

Questions that can't be answered from DECISIONS.md + STATUS.md +
README.md + this file: `git log --format='%an %ae' | sort -u` is the
starting point. Commit messages are deliberately detailed; most
integration decisions are documented in the commit that made them.

Good luck. The spine is solid, the wizard works, voice connects —
the product is ready to iterate.
