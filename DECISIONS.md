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

### Personality adjustment
- `adjust_personality(axis, delta)` — explicit user-directed personality shifts

Orb visual control (variant, palette, params, presets) is handled outside the LLM tool surface.

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
- **Self-modification via conversation.** Orb appearance changes are
  driven by external process signals, not LLM tool calls.
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

## Amendment — 2026-04-23: orb state + mood authoring

The orb's visual reacts to both voice state and mood. Authoring those
reactions needs to be a first-class editor surface.

- **Voice states** are `idle`, `listening`, `thinking`, `speaking`.
  "Breathing" is not a separate state — it's the ambient animation
  layer present in all four, with per-state intensity.
- **Mood dimensions** are `valence`, `arousal`, `guardedness` (see
  memory/personality.py). Each is in [-1, +1] and drives shader
  uniform deltas.
- **Preset shape is deltas, not absolutes.** A preset stores
  `(variant, palette, base_params)` plus `state_overrides` (per voice
  state) and `mood_overrides` (per mood dim) as deltas applied on
  top of base. This composes cleanly — a speaking+cynical orb is
  base + speaking delta + cynical delta, not a separately-authored
  (speaking, cynical) cell.
- **Editor gating:** the full state/mood authoring editor is part of
  the paid customization unlock. Free tier runs a starter preset
  that's pre-authored; free users don't get the authoring tool.
- **Config file stays one.** `config/orbis.yaml` grows to hold
  `state_overrides` + `mood_overrides` per preset rather than
  adding a second file.

Task impact: task #53 (mood + visual reflection) is now the
reflection engine *plus* the authoring editor. Task #55 (config UI
mirror) now has more surface to mirror. Task #59 (hatch animation)
benefits from the state-authoring tooling because hatch is a
state-transition timeline.

## Amendment — 2026-04-23: docker default is GPU-first

The original "no GPU dependency" statement in § TTS providers is still
true for kokoro itself — it runs fine on CPU. But the default docker
path now reserves an NVIDIA GPU for the orbis service so Whisper STT
and Kokoro both run on CUDA. Reason: CPU Whisper is multi-second per
utterance — the single biggest latency source in a turn. Voice-first
as a product promise is unreachable without that acceleration.

- **Default:** `docker compose up` requires an NVIDIA GPU + driver ≥
  570 + `nvidia-container-toolkit`. Torch is pinned to a `+cu128`
  wheel in the Dockerfile so it matches the container's CUDA 12.8
  base image.
- **CPU-only override:** `docker-compose.cpu.yml` strips the GPU
  reservation (`!reset []` on the device list) and swaps `runtime`
  back to `runc`. Users layer it with `-f`:
  `docker compose -f docker-compose.yml -f docker-compose.cpu.yml up`.
  The app still works, it's just slower.
- **Native `python app.py`** is unchanged and remains fully CPU-
  viable — no toolkit requirement, no override file. The GPU-first
  posture is strictly a docker concern.

## Amendment — 2026-04-24: LLM factory + MLX-LM as Apple-Silicon default

The LLM has graduated from "single OpenAILLMService talking to whatever
URL is configured" to a small adapter pattern under `voice/llm/`:

  voice/llm/__init__.py     — make_llm() factory + provider auto-detect
  voice/llm/openai.py       — re-export of pipecat's OpenAI-compat path
  voice/llm/ollama.py       — native /api/chat (so `think: false` works)
  voice/llm/mlx.py          — Apple Silicon native via mlx-lm

Selection precedence (in `make_llm`):

  1. Explicit `provider="..."` kwarg
  2. `mlx://<huggingface-id>` URL scheme  → MLXLLMService
  3. URL shape (port 11434, "ollama" hostname) → OllamaLLMService
  4. Probe `<root>/api/version` 200 → OllamaLLMService
  5. Fall back to OpenAILLMService

Why each adapter exists:

- **Ollama-native** — Ollama's OpenAI-compat /v1/chat/completions
  silently ignores the `think: false` request field. Models with
  reasoning preambles (gemma3/4, qwen3, deepseek-r1) jam pipecat's
  sentence aggregator until the reasoning phase ends. Native
  /api/chat honors `think`; first content tokens land in 100-300ms
  instead of 6-8s.
- **MLX-LM** — Mac users no longer need a separate Ollama install.
  Models download into the HF cache the same way Whisper and Kokoro
  already do; the LLM runs in-process inside the Python sidecar.
  ~2× faster than llama.cpp on Apple Silicon for the same
  quantization. Lazy-imported so non-Mac builds keep working without
  the dependency.

Default desktop wizard preset is now `mlx-community/Qwen3.5-4B-MLX-4bit`.
The OllamaInstallHelper preset stays available for users who already
run Ollama or want to share models with other tooling. We deliberately
don't auto-upgrade Ollama users to MLX — a multi-GB silent download
under the user violates the "no surprises" principle.

This decision pushes the explicit cross-platform desktop story:
**Apple Silicon Mac is the supported desktop product. Linux/Windows
desktop builds remain in CI for completeness but are deprioritized;
the supported answer for those platforms is the Docker self-host
path that's already documented.**

## Amendment — 2026-04-24: Tauri shell + WebContent media capture

> **Superseded by 2026-04-29 — Tauri shell removed in commit 9b52d97.
> See the next amendment below for the WebRTC-PWA path that replaced
> it.**

The Tauri 2 desktop shell now ships with three runtime patches that
make the WKWebView's WebContent subprocess actually usable for
real-time voice on a Developer-ID-signed Mac build:

- `src-tauri/src/mic_permission.m` — calls
  `AVCaptureDevice.requestAccessForMediaType:` at app boot so TCC
  registers our bundle id. Without this, our app never appears in
  System Settings → Privacy & Security → Microphone.
- `src-tauri/src/media_permission_patch.m` — runtime swap of wry's
  WKUIDelegate. wry hardcodes `WKPermissionDecision::Grant` for
  media capture, which bypasses TCC and hands WebContent a dead
  audio stream. We replace the decision with `Prompt`, which routes
  through TCC properly. Re-applies on a 1-second heartbeat so
  reload / new-webview events get caught.
- `src-tauri/entitlements.plist` — adds `audio-input`, `camera`,
  network, JIT, library-validation exceptions required by hardened
  runtime + WebContent.

These patches are Mac-specific (no-op on other platforms via cfg).
They're considered part of the supported architecture, not
workarounds — wry's media-capture default isn't going to change
upstream anytime soon, and the Apple-side requirements are stable.


## Amendment — 2026-04-29: drop the Tauri shell — distribute as a WebRTC PWA backed by the Python sidecar

Reverses the prior Tauri amendment. The desktop shell sat between us
and shipping for two weeks: WKWebView's IPC custom-protocol POST bug
broke fetch handshakes, the wry UIDelegate patch needed re-application
heartbeats, the pyapp sidecar cache had to be manually cleared on
every rebuild, and the signed/notarized DMG path added a release-
pipeline tax. None of those costs paid for themselves on a single-
user product where the *server* already knows how to host itself.

Going forward:

- **No Tauri, no wry, no Rust shell.** The webapp is a PWA served by
  the Python sidecar at `/`. Same FastAPI app that handles `/api/*`
  also serves the built `web/dist/`. Ships as one process.
- **Distribution path:** `pipx install orbis` / `uv tool install orbis`
  → `orbis serve --open` opens the browser at 127.0.0.1:7866. Phase
  pending in #49 (cut 0.2.0 release).
- **Mic permission** is the browser's problem, not ours. Browsers ship
  the TCC dialog through the platform's media-capture flow without
  any wry-style intermediaries.
- **All `src-tauri/`, `Dockerfile.fish`-adjacent shell code, and the
  `desktop-build.yml` CI workflow are deleted** in commit 9b52d97.
  History is searchable but the artifacts are gone.
- **Voice-lifecycle docs** (`docs/voice-lifecycle.md`) had a
  Tauri-shell + sidecar-spawn section; scrubbed.

This is in service of a broader principle:

### Sub-amendment — 2026-04-29: thin sidecar, offload to client + microservices

Where we can offload work from the Python sidecar to either the
browser or an external microservice, we do. The server's job is LLM
routing + persona logic + memory; everything else is a candidate for
removal. Concrete:

- **Whisper STT moved to the `[whisper]` pip extra** (commit 6569aad).
  Default install no longer pays the ~500MB transformers/accelerate
  footprint. Default `STT_BACKEND` is now smart — `local` if the
  extra is installed, else `openai` (compat endpoint).
- **Web Speech client-side STT** (T61, pending) — browser does the
  STT, server receives transcripts via custom RTVI message. Whisper
  becomes truly opt-in for offline / privacy-sensitive deployments.
- **Deepgram + AssemblyAI / Cartesia / Soniox streaming** (T63,
  pending) — server-driven streaming as the alternative to in-process
  Whisper. Same UI elevation pattern as STT/TTS lifts.
- **kokoro extra + speechSynthesis** (T62, future) — same treatment
  for the TTS half once the STT path is settled. Eventually a
  torch-free default install.

### Sub-amendment — 2026-04-29: elevate config to the UI as default

Env-only knobs that affect user-facing behaviour belong in the
settings UI, not in `.env`. ORBIS is meant to be a self-installable
consumer product; anything that requires editing a dotfile breaks
that promise. Already-lifted knobs:

- LLM provider URL/Model/API key (commit 53e9b78 + earlier)
- OpenAI-compat TTS URL/Model/API key (commit bc1c57c)
- STT backend + Whisper model + URL/Model/API key (commit efedf05)
- Owner API key (relocated 53e9b78, then dropped d302d9e since
  single-owner installs don't need a UI to enter X-API-Key)

When introducing or touching a knob currently env-only that the user
might reasonably want to change, surface it in the panel by default;
don't ask "should we?". Secrets get the LLM-panel treatment ("leave
blank to keep" placeholder; never echo the saved value back).

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
