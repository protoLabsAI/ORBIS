# ORBIS — Locked Decisions

Frozen snapshot of architecture decisions reached 2026-04-23. This
file is not a design doc — there's no roadmap, phases, or "what to
build first." It's an inventory of what was *decided*. Implementation
details that don't constrain architecture are out of scope here.

Any decision that contradicts this file requires an explicit amendment
(add a `## Amendment — YYYY-MM-DD` section below with the reversal +
reason). Don't silently change direction.

---

## Product

- **Voice-first AI companion.** Realtime bidirectional voice is the
  defining interaction; text/chat is a secondary accessibility mode,
  not the pitch.
- **Router-first capability model.** The orb's primary value is
  delegating to the user's own configured agents (A2A, OpenAI-
  compatible endpoints). ORBIS is not an agent framework in the pile;
  it's a voice frontend for agents the user already has.
- **Differentiated by:** persistent memory, slow-drift personality,
  mood state, soft-neglect behavior, visible personality state. These
  are what make it a companion rather than a voice adapter.
- **Adults only.** Not pitched at minors; safety posture is built for
  25-40yo target demographic.
- **Single-user, single-owner, multi-device.** User hosts their own
  instance on tailnet; phone + PC + whatever all hit the same
  instance. No multi-tenant isolation.

---

## Architecture

### Voice pipeline
- **Pipecat stays as-is.** WebRTC, STT, TTS, VAD, voice-quality
  controllers (filler, backchannel, barge-in, micro-ack, echo-guard,
  prosody, delivery) — all kept.

### TTS providers
- **kokoro default.** CPU-friendly, runs on broader hardware, no GPU
  dependency, no Fish server needed.
- **Optional alternates:** ElevenLabs, OpenAI-compatible TTS URL
  (user-configurable). BYO keys.
- **Voice cloning:** dropped entirely. `/api/voice/clone` and related
  endpoints deleted. Users who want custom voices bring their own
  Fish or clone service and point the OpenAI-compat URL at it.
- **Fish TTS:** retained as an opt-in path (not default, not in default
  image). No `Dockerfile.fish` in default build.

### LLM
- **Small/fast LLM for the voice agent itself.** Qwen-tier or similar —
  the voice brain is a router + personality layer, not a heavy
  reasoner.
- **Heavy reasoning via `delegate_to`**, not in-process. No bundled
  protoAgent. No in-process LangGraph. Smart agents are external
  delegate targets.

### Auth
- **Single-owner primitive kept.** API key verification stays so a
  tailnet neighbor can't use your orb. But the multi-tenant machinery
  (user roster, `allowed_skills`, admin/user roles, `pinned_viz`)
  is deleted.

### Skills
- **Skills system deleted entirely.** One orb persona per install.
  The `skills/` Python package, `config/skills/*.yaml` catalog, the
  YAML-inheritance loader, per-skill prompt/voice/tools overrides —
  all gone.
- Persona and voice **are user-configurable** but via a single config
  file, not a catalog of interchangeable personas.

---

## Tool surface

The ORBIS voice agent has a deliberately small tool surface. Heavy
capability comes through delegation.

### Primary
- `delegate_to(target, query)` — the spine. Existing A2A + OpenAI-
  compatible delegate infrastructure retained unchanged.

### Orb self-modification (new — voice agent can tune its own face)
- `set_variant(name)`
- `apply_palette(name)`
- `adjust_param(key, value)`
- `save_preset(name)`
- `recall_preset(name)`

### User-interaction primitives (optional — add if useful)
- `remember(fact)` — explicit commit to long-term memory
- `show_inbox(text)` — push to chat without speaking aloud
- `confirm(prompt)` — pause voice, wait for yes/no

### Deleted from the seed
- `calculator`, `get_datetime`, `web_search`, `fetch_url`,
  `slow_research`, `a2a_dispatch` — all become user-configured
  delegates if the user actually needs them.

---

## Memory

- **SQLite, single-file embedded.** No graph DB service, no Neo4j,
  no Postgres, no vector DB daemon.
- **Tables (shape, not schema):**
  - `sessions` — one row per voice session, atomically persisted at
    session end
  - `facts` — with `valid_at`, `invalid_at`, `confidence`, source
    episode reference (Graphiti-shaped but SQL-native)
  - `personality_axes` — one row per axis, drift value + timestamp
  - `personality_events` — time-series of drift events (optional)
  - `mood` — short-term emotional state
  - `entitlement_cache` — local mirror of Stripe verification
- **FTS5** for text retrieval on sessions and facts.
- **`sqlite-vec`** for semantic search — optional, not day one, added
  only if FTS5 + BM25 proves insufficient.
- **Curator** applies 90-day confidence half-life decay on facts
  (protoAgent pattern, already in-tree). Prunes below ~0.2 confidence.
- **Optional background entity-extractor agent** does LLM-driven fact
  mining from raw episodes. Opt-in, not hot-path, not day one.
- **Per-user scope only.** No per-skill keying (skills are gone). Just
  `(user_id, *)`.

---

## Personality

- **Many axes, Seaman-flavored.** Not 3-4; many. Spanning mood
  (warm/guarded, playful/serious, hopeful/cynical), rhetorical style
  (sarcastic/sincere, verbose/terse, grandiose/grounded), curiosity
  shape (probing/incurious, philosophical/pragmatic), neediness
  (independent/clingy), etc. **Exact set chosen at implementation.**
- **Drift: both directable and automatic.**
  - Directable: user says "be more playful" — sticks.
  - Automatic: axes drift from interaction patterns over weeks.
  - Neither dominates; both compose.
- **Mood state:** shorter-term than personality. Visible in orb
  visuals (slow cool pulse vs rapid warm turbulence vs desaturation).
- **Soft-neglect kicks in over days.**
  - Day 2-3 of silence: mood visibly shifts
  - Day 4-7: noticeable guardedness
  - Return: "relieved to see you" warmup
  - **No death.** No mortality stakes. Adult product; grief mechanics
    are for teen-oriented pet games.
- **Visible personality state:**
  - Implicit (primary): voice + behavior + orb visuals reflect the
    current state.
  - Explicit (secondary): Profile panel in the drawer surfaces mood +
    axes + recent memory highlights for users who want to peek.

---

## Visualization

- **Existing orb stays.** R3F + variant registry (Fractal, Nebula,
  Crystal, Particles) + shared driver hooks + broadcast bus +
  localStorage preset store. All inherited from protoVoice.
- **Starter orb acquisition:** user picks one of N from a curated
  pool shipped with the binary. Not random — user chooses. Starter
  pool definition is implementation detail.
- **Self-modification via conversation.** The orb-control tools above
  let the agent change its own appearance in response to user requests.
- **Paid unlock:** full editing of all variants + all palettes +
  per-param tweaking behind a one-time purchase.

---

## Monetization

- **Free tier:** one starter orb (picked from the curated pool),
  full companion experience (memory, personality, voice, delegation).
  A complete product on its own.
- **Paid tier:** one-time Stripe purchase unlocks full customization
  (all variants, all palettes, per-param editing) + whatever future
  shop items get added.
- **Entitlement model:** phone-home verification against Stripe + local
  N-day cache for offline tolerance. User tells us the cache window;
  default TBD.
- **Explicitly not doing:** gacha, loot boxes, energy timers,
  pay-to-progress, game-mechanic collectibles, cosmetic FOMO cycles,
  season passes, subscriptions (for v1; revisit later if needed).
- **Future:** shop for special/seasonal orbs. Deferred.

---

## Configuration

- **Format:** YAML.
- **Shape:** one main config file in the repo tree (`config/orbis.yaml`
  or similar). Single source of truth.
- **UI mirror:** all user-editable settings exposed in the drawer /
  settings UI. UI reads from and writes back to the file. Reload-on-
  write on the server side.

---

## First-run experience

- **Setup wizard (UI).** Runs on first boot. Flow TBD but shape is:
  set auth key → pick starter orb from N → configure TTS provider →
  add first delegate (A2A or OpenAI-compat) → hatch.
- **Hatch animation.** One-time, unique to the installation, orb
  forms and speaks first words. Specific animation TBD.

---

## Deleted from the protoVoice seed

Concrete carve list (enforced by subsequent commits):

- `skills/` Python package
- `config/skills/*.yaml` catalog
- `config/SOUL.md` as a skill-system dependency
- Multi-tenant parts of `auth/users.py` (`allowed_skills`, role split,
  pinned_viz, full roster)
- `/api/whoami`, `/api/users/reload`, `/api/admin/*`, `/api/skills/*`,
  `/api/voice/clone`, `/api/voice/references`
- `web/src/auth/` (whoami store + role-gating hooks)
- `SkillSelector.tsx`, admin tab gating, lock-chip branches
- `Dockerfile.fish` in the default build path
- Fish service in `docker-compose.yml` default profile
- Most of the v0.12.1 test suite (users, endpoint admin variants,
  allowed_skills cases)
- `pyproject.toml` heavy deps that the voice stack doesn't actually
  need (vllm, torch for anything other than local audio model,
  transformers unless Whisper is local, etc. — case by case)

## Deferred / not blocking

- Exact personality axis set (many, Seaman-flavored — at implementation)
- Soft-neglect exact thresholds (days — tuned at implementation)
- Starter orb pool (N count, specific variants + palettes)
- Setup wizard UI details
- Hatch animation design
- Stripe integration specifics (cache TTL, webhook exact shape)
- Pluggable-TTS layer wire-up specifics

## Explicitly out of scope

These were considered and rejected during the design conversation:

- Bundled/vendored protoAgent (rejected in favor of pure delegation)
- OpenAI Realtime API as the voice stack (rejected — economics + vendor lock)
- Collectible orb economy with rarity tiers (rejected — wrong game)
- Idle-game progression (Resonance, Attunement, Rebirth) (rejected — wrong game)
- Sanctum-as-visitable-space, asynchronous social (rejected — out of product scope)
- Multi-tenant roster with per-user `allowed_skills` (rejected — we're single-user)
- Skills-as-personas catalog (rejected — one orb per install)
- Fish TTS as default (rejected — broader hardware support via kokoro)
