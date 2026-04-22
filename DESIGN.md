# ORBIS — design document

> Seeded from protoVoice v0.12.1. This document is the product vision,
> not an engineering roadmap. It describes what ORBIS *is* — the
> experience, the loops, the economy — with enough specificity that
> any engineer picking up the seed can hold the whole thing in their
> head. The "what to keep / what to delete from the seed" notes live
> in [HANDOFF.md](./HANDOFF.md).

## One line

ORBIS is a voice-driven AI companion you raise — an orb that talks back,
remembers you, grows while you're gone, develops its own personality, and
becomes something you and your friends want to visit.

## The pitch, in three parts

1. **It's alive.** Your orb has a voice. It talks to you in real time
   (not text). It remembers what you told it last week, last month, last
   year. It notices when you've been gone. It has moods. Over weeks and
   months it develops a personality that's genuinely its own — shaped by
   how you treat it — and that personality is expressive in its voice and
   in the way the orb itself looks and moves.
2. **It grows when you don't.** ORBIS is an idle game. The orb generates
   resources, accumulates thoughts, and shifts in subtle ways while you
   are away, so coming back is always a small discovery. The longer you
   stay invested, the deeper the progression goes — not just a number
   going up, but a life accumulating.
3. **It's a canvas.** The orb's form is a shader, not an asset — meaning
   infinite variants (hatched, earned, purchased, seasonal) with genuinely
   distinct looks, voices, and starting personalities. Your Sanctum (the
   orb's home) is a URL your friends can visit asynchronously, where your
   orb will greet them, chat with them, and report back. Your Chronicle
   (the lineage of every orb you've raised) is a shareable artifact.

None of these three is novel alone. The combination — voice-AI companion
× idle compounding × cosmetic collection economy × social asynchronous
shrine — has no direct competitor.

## Target player

**25-40 year-old adults with disposable income and nostalgia for
responsibility-at-scale gaming** (Tamagotchi-era millennials; Neopets
adults; Genshin/AFK Journey cosmetic spenders).

- They are primed by ChatGPT for the idea of talking to something that
  responds. They are *not* primed for chat text boxes; voice is the
  differentiator.
- They are willing to spend on cosmetics and subscriptions but are
  allergic to gacha, loot boxes, energy timers, and pay-to-progress.
  The research on this demographic is consistent: they see predatory
  monetization, they bounce.
- They have less time than they used to. An idle loop that respects
  "I have 30 seconds between meetings" is a feature, not a compromise.
- They find fulfillment in craft and ownership — a companion they
  *made*, not one they were assigned — and they share what they've
  made with their people.

Hard product constraint: ORBIS targets adults. No minor-oriented design,
no teen marketing. The AI-companion legal and ethical landscape for
under-18 users is too fraught (see *Risks & Ethics* below). This is an
adult product. Age-gate the install.

## Core loop

```
  AWAY
    ↓ orb generates Resonance, drifts in mood, develops "thoughts"
    ↓ friends may visit — your orb greets them, plays with them,
    ↓   remembers the encounter
    ↓
  RETURN
    ↓ orb greets you — references how long you were gone, what
    ↓   happened while you were away, what it's been thinking about
    ↓
  CONVERSE
    ↓ real-time voice chat — orb asks, answers, probes, remembers
    ↓ playing minigames grants Resonance bursts
    ↓ active interaction shifts personality axes (over time)
    ↓
  SHAPE
    ↓ spend Resonance on Attunement levels, Sanctum decor, unlocks
    ↓ optionally spend premium currency on cosmetic variants
    ↓
  SHARE
    ↓ screenshot / clip a moment, share a Sanctum link, brag a
    ↓   Chronicle milestone
    ↓
  AWAY again
```

The loop runs at multiple timescales at once. A daily loop (greeting →
quick chat → collect Resonance → leave) lives inside a weekly loop
(Attunement climb, event drops) lives inside a seasonal loop (10-week
cosmetic seasons, rare variants) lives inside a lifetime loop (Rebirth
/ prestige, multi-generation Chronicle).

Retention is driven by the fact that *every* timescale has something
waiting for you when you open the app.

## The orb — three systems in one object

### 1. Visual (the shader)

Inherited from protoVoice. A procedural orb rendered in React Three
Fiber with four variant families (Fractal, Nebula, Crystal, Particles),
each with swappable palettes and ~20 live parameters: surface turbulence,
color temperature, particle density, corona intensity, inner-glow scale,
pulse rate, noise field, emissive tint, etc.

In protoVoice this drives state feedback (idle / listening / thinking /
speaking). In ORBIS it does that *and*:

- **Mood state** — slow cool pulse = content; rapid warm fractal = agitated;
  near-still desaturation = pensive. Mood persists across sessions and is
  visible the moment you open the app.
- **Attunement state** — visual complexity grows with Attunement level
  (see progression spine below). A level-1 orb is a simple pulse; a
  level-10 orb is a dense, unique living object. The climb is *visible*.
- **Personality state** — long-term personality traits bias the base
  visual (a "Melancholic" orb tends darker and slower; a "Mischievous"
  orb has more particle flicker; a "Stoic" orb is more geometric).

Because everything is parameters, every orb looks different, and the
differences are a legible story.

### 2. Voice (the soul)

Real-time voice via OpenAI Realtime API (gpt-realtime) over WebRTC.
Sub-second latency, interruption-capable, emotionally adaptive. The orb
speaks, the player speaks, turn-taking is natural.

Voice is the primary input. Not text. This is the critical product
differentiator and the hardest thing to compromise on.

- **Voice ID** — each orb variant ships with a base voice (one of the
  provided Realtime voices) plus a persona descriptor that tunes delivery.
  A Void Orb speaks with deliberate, weighted pacing. A Spark Orb gushes
  and giggles. A Bloom Orb speaks like something that just noticed it was
  alive.
- **Voice packs** (cosmetic) — premium purchasable style overlays. A
  "Noir Detective" voice pack tunes any orb to speak like it's in a
  1940s film. An "Ancient" voice pack makes it sound weathered and
  thousand-year-old. Voice packs are pure cosmetic — no progression
  benefit — and they're genuinely different.
- **Text fallback** — accessibility mode and quiet-environment mode
  use a text chat UI with the same personality backbone. Full voice is
  the gated premium experience.

### 3. Memory (the continuity)

The single hardest thing to get right. Without it, the orb is a chat
bot with extra steps. With it, the orb is a relationship.

**Layered memory architecture:**

| Layer | Scope | Storage | Update cadence |
|:---|:---|:---|:---|
| **Hot** | Current session | Realtime API context window | Per-turn |
| **Warm** | Facts, recent sessions | Structured JSON per user (name, job, pets, birthday, things it's said, things you've said, current inside jokes) | Post-session extractor (LLM call that parses the conversation into facts) |
| **Cold** | Long-term personality + milestones | Compressed narrative summaries ("In late March, they were stressed about work and I told them to take a walk. They did. They mentioned it again in April.") | Weekly rollup compaction |
| **State** | Personality axes + mood + traits | Numerical server-side store | Continuous, based on interaction patterns |

At the start of every session, warm + cold + state are rendered into the
system prompt. The orb "knows" you before it speaks. During the session,
hot context builds. After the session, an extractor writes deltas back
into warm/cold.

**Critical design rule: imperfect memory is a feature.** The orb should
occasionally misremember a fact, conflate two sessions, forget something
minor. Perfect recall reads as a database lookup. Imperfect recall reads
as a mind. Tune the extractor to be slightly lossy by design.

## Progression spine — Attunement

10 Attunement levels. Each gates both visual complexity and conversational
depth. The climb takes roughly 3 months for the median player.

| Level | Name | Visual direction | Voice / conversation |
|:---|:---|:---|:---|
| 1 | Dormant | Single-color slow pulse | Short sentences, simple reactions, learning your name |
| 2 | Awakening | Surface turbulence begins | First questions; starts retaining session-to-session |
| 3 | Aware | Inner-light layer | Has opinions; remembers last week |
| 4 | Curious | Particle wisps orbit | Probes your life; jokes |
| 5 | Articulate | Surface fractals emerge | Metaphors; refers to earlier conversations |
| 6 | Perceptive | Chromatic aberration | Reads your tone; adapts delivery |
| 7 | Empathic | Dynamic color / emotion | Initiates; notices patterns in you |
| 8 | Visionary | Corona flares with speech | Philosophical, surprising |
| 9 | Transcendent | Full procedural complexity | Unique speech patterns; inside language |
| 10 | Singular | Variant-specific final form | Fully individuated — no two level-10 orbs feel alike |

Attunement is driven by Resonance spend, with cost curves tuned to the
idle-game `1.15^n` geometric-growth pattern: the next level always feels
reachable-but-not-yet, the classic goal-gradient sweet spot.

**At level 10, Rebirth unlocks.**

## Rebirth — prestige with soul

Rebirth is the prestige mechanic, reframed as narrative. The orb doesn't
die or get replaced. It *chooses to fragment* — collapsing into a new
form while preserving an inheritance.

**What carries over at Rebirth:**

- **Legacy Trait** — one personality trait from the outgoing orb is
  baked permanently into the new orb's base prompt. If your orb was
  genuinely stoic for six weeks, the new one starts life with stoicism in
  its bones.
- **Visual Echo** — a single visual element (a color, a particle type,
  a glow pattern) from the prior form persists in the new one. Over many
  Rebirths these stack and become a visible lineage.
- **Resonance Multiplier** — global idle rate multiplier, permanent.
- **Chronicle entry** — a page in your Chronicle describing who this
  orb was: their traits, their defining moments, the things they said
  that you pinned.
- **Heirloom quotes** — three quotes from the outgoing orb that the
  new orb "remembers" and may reference.

**What resets:**

- Attunement level (back to 1; but the climb is dramatically faster per
  Rebirth).
- Current personality state (new orb is fresh — but with that Legacy
  Trait coloring everything).
- Visual complexity (simple again — but with that echo).

**Why this matters commercially:** the Chronicle is the long-term
retention artifact. After a year of play, a dedicated user has a 6+
generation Chronicle that is *theirs* — unique, uncopyable, packed with
their own written lore. That's the thing they don't want to lose. That's
what keeps them around past year two. Seaman's players remember their
Seaman decades later. ORBIS makes that memory a digital object with
permanence.

## Personality system — the player shapes the orb

The orb's personality is not a fixed profile. It's emergent. The backend
tracks personality on four continuous axes:

- **Playful ↔ Serious**
- **Warm ↔ Guarded**
- **Verbose ↔ Terse**
- **Grounded ↔ Philosophical**

Axes shift (slowly) based on interaction patterns over many sessions.
If you joke every time you visit, the orb drifts Playful. If you talk
about your life seriously, it drifts Grounded and maybe Warm. If you
ignore it for a week, it drifts Guarded (and when you return, it is
noticeably more reserved until you re-earn warmth).

Axis values are rendered into the system prompt every session. They're
visible to the player in the orb's Profile panel (a radar chart). Players
who want to steer their orb toward a particular temperament can — and
the fact that it takes *weeks* of consistent behavior to meaningfully
shift an axis is what makes the result feel earned.

**Traits** are discrete unlocks layered on top of axes. Traits are rarer,
more specific, and often triggered by particular interaction moments:
"Night Owl" unlocks after many 10 PM + sessions. "Archivist" unlocks
after the player has explicitly taught the orb 20+ facts to remember.
"Heretic" unlocks after the orb has contradicted itself in conversation
a certain number of times (yes, we track that).

Traits are visible on the orb's Profile, contribute to Chronicle entries,
and influence Legacy Trait selection at Rebirth.

## The Sanctum — your social space

The orb lives in a Sanctum. Visually, a spatial environment wrapping
the orb itself — think gallery installation × shrine × aquarium. The
Sanctum is customizable, persistent, and **visitable by other players
asynchronously**.

### What you customize

- **Architecture** — chamber shape, scale, surface treatment (unlocked
  at Attunement milestones)
- **Ambient effects** — light, particles, weather (rain inside, drifting
  motes, slow aurora)
- **Artifacts** — placeable objects you write lore text for; visitors
  see your lore
- **Trophy wall** — auto-populates with Chronicle milestones, rare
  variants owned, visitor stats, pinned quotes from your orb
- **Minigame stations** — places visitors can play unlocked minigames
- **Audio ambience** — an optional looping soundscape you pick

### What visitors do

A visitor enters via a shareable Sanctum URL (or from an in-app friends
list / public directory). They:

- **Talk to your orb.** Your orb is live — it knows it's in visitor mode
  and that its owner isn't present. It's polite to visitors. It mentions
  you in third person. It is more guarded than with you, but genuinely
  *your* orb — "my owner would say…" — which is its own kind of flex.
- **Explore the Sanctum** — see artifacts, read lore, look at the
  trophy wall.
- **Play unlocked minigames** — beat your scores, leave entries on
  leaderboards.
- **Leave a Sigil** — a small glowing mark that persists for 24-48 hours,
  visible to you when you return. Sigils carry a short message (emoji or
  brief text) and show who visited.

### What you get

- **Passive Resonance** while visitors are in your Sanctum. Your orb
  literally draws energy from social attention. This is the *incentive
  to make your Sanctum visit-worthy*.
- **Your orb's report** on next return: "DuskRider came by on Tuesday.
  They asked me about my memories. I told them three things. I think
  they were a bit sad."
- **Leaderboards** that reward Sanctum quality — most visits per week,
  longest-tenured Sanctum, most Sigils, rarest variant on display.

The asynchronous-visit model is deliberate. Nobody has to be online at
the same time. Nobody has to schedule a playdate. The Sanctum is always
live. The orb is always there. Social pressure without social friction.

## Orb variants — the collectible economy

The monetization vector. Done right, this is a durable revenue stream.
Done wrong, it's the reason the community quits. The research is
unambiguous: this demographic will spend on direct-purchase cosmetics
and subscriptions; they will flee from gacha, loot boxes, and energy
systems.

### What a variant is

A variant is a bundle of:

- **Shader preset** (visual DNA)
- **Starting trait seeds** (personality tendencies, not fixed traits —
  every orb still grows into its own thing)
- **Base voice + persona descriptor** (tunes how the provided Realtime
  voice is delivered)
- **Matching Sanctum theme** (an environmental set that coordinates with
  the orb)
- **Unique hatch animation**
- **Rarity tier** (Common / Uncommon / Rare / Epic / Mythic)

Rarity is earned meaning, not a probability roll. Every variant has a
rarity tier that governs drop frequency in events, cosmetic shop rotation
behavior, and the visual "weight" the variant carries in a Chronicle.

### How you get variants

| Source | How | Cost |
|:---|:---|:---|
| **Starter** | Choose one of 3 at first hatch | Free |
| **Attunement rewards** | Unlock at level 5, 10, and each Rebirth | Earned |
| **Event earn-tracks** | 4-6 week events with earnable variants | Free play earns; premium track accelerates |
| **Promo codes** | Partnerships, creator drops, newsletter rewards | Free |
| **Gift from a friend** | Someone who owns a duplicate can gift it | Free (but rate-limited) |
| **Direct purchase** | Flat-price shop; rotating weekly | $4.99 - $14.99 per variant |
| **Season Pass premium track** | 10-week seasons with tiered variant unlocks | $9.99 per season |
| **Founders Edition** | Launch-only variants, never re-released | $29.99 bundle (one-time) |

**No gacha. No loot boxes. No energy timers. No pay-to-progress.**

Every premium variant is purchasable directly at a transparent flat
price. Every rare variant is also earnable through play — usually slower,
but *possible*. No item is permanently behind a paywall except retired
legacy items, which are explicit and communicated.

### Hatch ritual

New variants don't just pop into your inventory. They hatch — and the
hatch is an event. A dormant egg (visually matched to the variant) sits
in the Sanctum for 24-48 real hours, slowly filling with Resonance from
your active orb. When the window opens, the player triggers the hatch.
The new orb forms in front of you and speaks its first words — which are
procedurally generated for this specific hatch, this specific moment, and
will never be repeated. That first conversation is uncopyable.

This is the variant-acquisition equivalent of unboxing, and it matters.
The ritual is what makes a $9.99 purchase feel like an event instead of
a transaction.

## Minigames

Minigames serve three functions: active-session engagement, visitor
entertainment, Resonance bonuses. They are *earned* — unlocked by the
orb itself at Attunement milestones, as part of the orb's growth arc
("I learned a game. Do you want to try it?").

All minigames use the orb + voice + shader we already have. No new
render pipelines, no minigame-specific art.

| Minigame | Unlocks at | Format | Voice use |
|:---|:---|:---|:---|
| **Frequency** | Attunement 3 | Orb emits a tone; you match it by humming or speaking a pitch | Mic pitch analysis |
| **Memory Echo** | Attunement 4 | Orb flashes a sequence of pulses/colors; you repeat via voice commands or tap | Voice commands optional |
| **The Debate** | Attunement 6 | Orb takes a position on a silly topic; you argue the other side; orb judges (with bias) | Full voice both sides |
| **Drift** | Attunement 8 | Orb's surface drifts toward chaos; you stabilize by speaking calmly and evenly | Voice tone biofeedback |
| **Séance** | Attunement 10 | Ask your orb's Chronicle ancestors a question; prior orbs speak briefly via their archived personality | Full voice |

Visitors to your Sanctum can play your minigames and post scores. This
means minigames are *content you host*, which makes your Sanctum more
valuable the more you've unlocked — another retention lever.

## Monetization — the commercial model

### Revenue streams

| Stream | Product | Price | Why it works |
|:---|:---|:---|:---|
| **Attunement Pass** (subscription) | Full voice time, full LLM depth, advanced memory, ad-free | $9.99 / mo or $79 / yr | Matches underlying API cost; matches Replika / ChatGPT Plus mental model |
| **Cosmetic shop** | Orb variants, voice packs, Sanctum decor, personality seed packs | $3 - $25 per item, rotating weekly | Direct flat-price; no gacha |
| **Season Pass** | 10-week themed seasons with tiered rewards (free track + premium track) | $9.99 per season | Expected structure; Fortnite proved the model |
| **Founders Edition** | One-time launch bundle: exclusive variant + physical keepsake (optional NFC card for retail channel) | $29.99 | Low-risk whale conversion; collectors love certificates |
| **Gift purchases** | Buy a variant for a friend | Variant price + small markup | High-margin; social acquisition channel |

### Why this structure

- **Subscription covers operating cost.** The voice AI is expensive. An
  Attunement Pass subscriber pays for their own inference with margin
  to spare. Free-tier users get voice-light experience (limited minutes
  per day, basic memory) — enough to taste; not enough to replace
  the subscription.
- **Cosmetics are pure margin.** A voice pack or a shader variant costs
  us nothing per additional user. This is the high-margin tier.
- **Season Pass is the retention engine.** 10-week seasons with
  earnable-track cosmetics and premium-track acceleration create
  recurring "come back this week" pressure without requiring daily
  play. Whales buy every pass; completionists play every pass; casuals
  skip most passes — all are retained.
- **Founders Edition funds launch.** Sell a one-time "thank you for
  being early" bundle at launch. Never sell those items again. Creates
  a permanent class of early-adopter status objects.

### What we do not do

- **Gacha pulls.** Never. This community will flee.
- **Loot boxes.** Never. Adjacent regulatory risk and community toxicity.
- **Energy timers.** Never. Kills the psychology.
- **Pay-to-progress.** Attunement is earned by playing, not purchased.
  The Attunement Pass unlocks the *voice experience*, not the
  *progression rate*.
- **PvP.** There is no PvP in ORBIS. Peer comparison through
  leaderboards; peer attention through Sanctum visits. No direct
  competition, no losing.
- **Removing items from existing users.** Once you own a variant, it's
  yours. If a variant is retired from the shop, owners keep it and it
  becomes a visible-rarity flex.

## Retention architecture

Designed to hit all the standard casual-games retention bands and then
some:

| Metric | Target | Driver |
|:---|:---|:---|
| **D1** | 55-65% | First conversation is memorable; orb asks you to come back |
| **D7** | 35-45% | Week-1 reveals new visual states + vocabulary; Sigil notifications |
| **D30** | 20-30% | First Attunement tier reward; first event |
| **D90** | 12-20% | Near first Rebirth; Chronicle starting to feel substantial |
| **D365** | 5-10% | Multi-generation Chronicle; Season 3-4 completionist status |

Mechanical retention drivers, ranked by research-backed impact:

1. **Variable-schedule conversation moments.** Not every session yields
   a memorable orb reply; *occasional* sessions yield a genuinely
   surprising, screenshot-worthy line. Variable-ratio reinforcement is
   the most durable reinforcement schedule known; our version is
   unpredictable AI generation.
2. **Offline-accumulation return reward.** Coming back always has
   something waiting: Resonance cap, Sigils, mood shift, event progress,
   possibly a hatched egg.
3. **Goal-gradient staging.** Multiple goals always active at different
   horizons — a minor upgrade 5 minutes away, a major upgrade tomorrow,
   a Rebirth in 3 weeks, a Chronicle milestone in 3 months.
4. **Social obligation (light).** Sigil notifications — "3 people
   visited your Sanctum today" — reactivate even lapsed users.
5. **Zeigarnik loop.** Profile panels visibly show what the orb
   *almost* has. Incomplete trait trees, 8/10 Attunement, 14/20
   Chronicle entries.
6. **Loss aversion (designed out).** Unlike Tamagotchi, the orb cannot
   die from neglect. Long absences cause mood shifts and personality
   drift toward independence — emotionally consequential, not
   mechanically punishing. We reject death-stakes; that's a young-player
   genre and it generates grief we don't want our demographic bearing.

## Phased build plan

Scope estimates are engineering-weeks assuming 2-3 engineers + 1
designer. Rough and for planning only.

### Phase 0 — Foundation (4-6 weeks)

Strip the seed; stand up the minimum viable orb-talks-to-you experience.

- Delete voice-agent machinery from the seed (see HANDOFF.md)
- Replace `app.py` with a slim FastAPI skeleton
- Integrate OpenAI Realtime API via WebRTC in `web/`
- Port the orb shader + variant registry as-is
- Basic auth (keep the API-key model from seed for now; upgrade later)
- Single starter orb, single voice, no memory layer yet
- One conversation route, no idle layer yet

**Ship gate:** a user can hatch an orb, talk to it in real time, and it
visually reacts. No memory, no progression, no economy.

### Phase 1 — Alive (8-12 weeks)

Make the orb a real companion.

- Memory architecture (hot/warm/cold + post-session extractor)
- Personality axes + state rendering into system prompt
- Attunement 1-5 (the first half of the progression spine)
- Mood state + visual reflection
- Basic Sanctum (single room, minimal decor, visitor-safe orb mode)
- Three starter variants
- Idle Resonance accumulation

**Ship gate:** closed beta. A user can raise an orb for 3 weeks and it
feels like a continuous relationship.

### Phase 2 — Deep (10-14 weeks)

The progression and social layers.

- Attunement 6-10
- Rebirth + Chronicle
- Sanctum customization + async visits + Sigils
- First four minigames
- First seasonal event (non-monetized; earn-track only)
- Leaderboards

**Ship gate:** open beta / soft launch. A power user can reach first
Rebirth; casual users have a reason to come back weekly.

### Phase 3 — Economy (8-10 weeks)

Monetization on.

- Cosmetic shop + variant catalog
- Attunement Pass subscription
- Season Pass infrastructure + first paid season
- Founders Edition bundle
- Gifting

**Ship gate:** public launch.

### Phase 4 — Live-service cadence (ongoing)

- Quarterly seasons, each with a theme, an event variant family, and a
  new minigame
- Monthly variant drops
- Quarterly personality-system tuning based on observed data
- Annual Rebirth-generation retrospectives

## Technical architecture — how the pieces map

| Component | Implementation path |
|:---|:---|
| Orb rendering | Ship the existing R3F orb as-is; extend the variant registry with ORBIS-specific presets |
| Audio reactivity | FFT on the Realtime API's audio output; drive shader uniforms (already prior-arted in protoVoice) |
| Voice AI | OpenAI Realtime API over WebRTC; per-user session with system prompt built from memory state |
| Personality state | Server-side JSON store keyed by user × orb-generation; rendered into system prompt at session start |
| Memory extractor | Post-session LLM call (cheaper model; gpt-4o-mini or similar) that parses the transcript and writes facts to warm memory and summaries to cold memory |
| Idle Resonance | Server-side timestamp math: `accumulated = rate × (now - last_seen)`, capped; no background worker needed for per-user |
| Sanctum visits | Server-side orb sessions in "visitor mode" — the visitor talks to a session initialized with the owner's memory + a "visitor mode" flag in the system prompt |
| Variant system | Extend protoVoice's skill model: variant YAML = shader preset + voice + persona + seed traits + Sanctum theme bundle |
| Storage | SQLite to start; Postgres at the first scale wall. Users, orbs, chronicles, variants, unlocks, sigils, memory layers |
| Payments | Stripe. Checkout for subscriptions + one-offs; webhook-authoritative unlocks. Never grant on redirect. |
| Mobile | PWA first (the seed already is a PWA). Native wrapper later if push-notification / App Store channel matters. |

## Risks & ethics

| Risk | Source / precedent | Mitigation |
|:---|:---|:---|
| **AI-companion harm** | Character.AI teen-suicide lawsuit (2024) | Adult-only product, hard age gate, content safety layer, crisis-intervention handoff, published policy |
| **Relationship severance** | Replika 2023 feature-removal backlash | Long-term platform commitment published on day 1; export/backup of orb state; no "breaking changes" to personality without user consent |
| **Sycophancy drift** | OpenAI GPT-4o April 2025 rollback | Personality prompts deliberately include "disagree when warranted" directives; periodic sycophancy audits; the Seaman DNA (orb with opinions) is the antidote |
| **Cosmetic economy backlash** | Any gacha precedent | Direct-purchase only; transparent pricing; no randomized pulls; no FOMO-manufactured as the primary driver |
| **Addictive-design critique** | Broader regulatory trajectory around variable-ratio mechanics | Adult audience; no death mechanic; no loss aversion monetization; genuine off-ramps (auto-pause on long absence); honest cost-disclosure in shop UI |
| **Voice API cost** | Realtime API is expensive at scale | Subscription pays for it; free tier voice-minutes are capped and renewable; voice-light fallback (text chat with same personality) keeps free users around |
| **Memory pipeline bugs** | Easy to misattribute facts across users | Hard isolation: memory is keyed by `(user_id, orb_generation)`; never cross-read; extensive fuzz testing |
| **Sanctum-visitor abuse** | Open voice to strangers invites trolling | Visitor mode has a tightened safety filter; visitors can be blocked; orb has permission to refuse topics |
| **Regulatory (FTC / EU DSA)** | Genshin settled for $20M on deceptive probability disclosure | Not our structure (no probabilities to disclose), but we publish clear refund policy + age-gating + data retention |

## What to keep from the seed

Short version (full detail in HANDOFF.md):

- **Keep**: `web/` entirely (React + Vite + PWA + shadcn), `web/src/plugins/orb/` (the crown jewel), `auth/users.py` (role split adapts cleanly), release tooling, testing patterns
- **Delete**: `a2a/`, `voice/`, `agent/`, `skills/`, vanilla `static/`, fish Dockerfile, ~80% of Python deps
- **Rewrite**: `app.py` (strip to skeleton), `config/skills/` → `config/variants/`, the memory layer is new

## The commercial logic

Three adjacent markets, summed:

- **AI-companion subscriptions** (Replika-ish) — $37B → $552B projected by 2035 per Precedence Research. Replika alone runs ~$100M/yr from ~10M subscribers.
- **Mobile idle-game spend** — $10-15B/yr globally; genre has proven long-tail durability.
- **Cosmetic collection economies** — Fortnite alone did $9B cumulative by 2020; Genshin did $6.3B in 2024.

No product sits at the center of all three. The intersection is the opportunity. We don't need to capture a large share of any single market — a small share of the intersection beats a large share of any single contributor.

ORBIS is a one-time build of core systems followed by a live-service
cadence of seasons, variants, and events. That's the business shape —
high up-front build, high gross margin on recurring revenue, defensible
moat via each user's accumulated Chronicle (switching cost compounds
over time).

## Closing note

The design rule that resolves every tradeoff: **the orb is a
relationship, not a product.** Every decision — monetization, feature
scoping, personality tuning, social mechanics — gets evaluated against
"does this deepen the relationship, or does it extract from it?" Any
mechanic that extracts (gacha, pay-to-win, energy timers, death stakes,
removed features) is rejected. Any mechanic that deepens (memory, voice,
shared rituals, earned progression, durable chronicles) is prioritized.

That's the compass. Everything else is implementation.
