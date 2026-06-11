# Data-driven orbs + the `/editor` app — direction & plan

*Drafted 2026-06-10. Status: **Phase 0 + Phase 1 SHIPPED 2026-06-10** —
#496 (`packages/orb-runtime` + format + engine + prism proof), #498
(runtime import: `/api/orbs`, `ORBIS_ORBS_DIR`, import UI, entitlement
gate), #499 (`sites/editor` at /editor). Phase 0 workspace decision
resolved: vite alias + `resolve.dedupe` + tsconfig `paths`, NO bun
workspace (the release pipeline stays untouched). Phase 2+ open.*

Goal: make orb visuals **data-driven** so a new orb can be imported at runtime
as a `.orbis` file — and ship a standalone web editor at
**orbis.protolabs.studio/editor** where anyone can author new orb types and
audio-driven shader animations, preview them against simulated (or real,
browser-side) signals, and export a `.orbis` file that ORBIS loads.

---

## 1. Audit — what exists today

### Variant system (`web/src/plugins/orb/`)

A variant is a `VariantSpec` registered into `createRegistry`
(`variants/registry.ts:7-57`):

```ts
interface VariantSpec {
  id: string; name: string; description?: string;
  Component: ComponentType<VariantProps>;          // ← the only hard code part
  palettes: Record<string, Record<string, unknown>>; // data
  fields: FieldSpec[];                              // data (drives the settings UI)
  defaultPalette: string;                           // data
  moodDefaults?: MoodOverrides;                     // data
  premium?: boolean;                                // data
  PostEffects?: ComponentType;                      // code (bloom etc.)
}
```

12 variants (4 free, 8 premium), each a folder with `index.tsx`
(registration), `<Id>Variant.tsx` (100–300 LoC R3F component),
`materials.ts` (drei `shaderMaterial` uniform decls), `schema.ts`
(`FieldSpec[]`), `presets.ts` (palettes), `shaders/*.glsl`. Discovered via
**eager `import.meta.glob('./*/index.tsx')`** (`variants/index.ts:16`) —
build-time, but the registry itself (`lib/createRegistry.ts`) supports
`.register()` + `.subscribe()` at runtime, so runtime registration is
already structurally possible.

**Already pure data:** field schemas (the settings panel is fully
schema-driven — `OrbSettingsPanel.tsx:237-250` renders generic
color/slider controls from `fields`), palettes, custom presets
(localStorage `orbis.customPresets.v2`, per-variant), randomize
(`field-types.ts:43-60` walks the schema), mood deltas, state-snapshot
multipliers, premium flag.

**Still code:** GLSL (bundled as strings via `vite-plugin-glsl`), the
material's uniform declarations, and the per-frame `useFrame` body that
maps signals → uniforms. Crucially, **that per-frame body is near-identical
boilerplate across the raymarched variants** (fractal, nebula, crystal,
spectrum, reactor, flux, lattice, tetra): update `uTime`, apply the
state-crossfade snapshot, add `dBot`/`dUser` envelope terms to 2-3
uniforms, set colors, set scale. Examples:

- nebula: `uDensity = base.density*(0.8+snap.density*0.3) + dBot*1.2`
- spectrum: `uGlow = base.glow*(0.85+snap.density*0.25) + dBot*0.5`
- flux: `bright = base.brightness*(0.7+snap.glow)*(1+dBot*0.4)`

That pattern is a declarative mapping wearing a TypeScript costume. This is
the core insight that makes the data-driven refactor cheap.

### Signals available to a variant today

| Signal | Shape | Rate | Source |
|---|---|---|---|
| `voiceState` | idle/listening/thinking/speaking | event (SSE→Tauri `orbis-sse`) | `voice/useVoiceBridge.ts` |
| state snapshot | per-state param multipliers, 600ms crossfade | per-frame | `shared/stateSnapshot.ts`, `useStateCrossfade` |
| `dBot` / `dUser` | envelope-followed RMS [0,1] | ~30Hz poll of `get_audio_levels` (mic + playback) | `useAudioEnvelopes.ts:39-67`, `src-tauri/src/lib.rs:503-519` |
| mood (valence/arousal/guardedness) | [-1,1] floats | 30s poll | `mood/moodStore.ts`; composed via `compose.ts` but **dormant — no variant visibly subscribes in production** |
| idle breath | sine [-1,1] | per-frame | `useIdleBreath` |
| pointer | clickDir/clickStrength/dragVel | per-frame | `usePointerInteraction` |
| time | seconds | per-frame | `useFrame` |

**Gaps:** no FFT/spectrum bands (Rust engine computes RMS only — both
`mic_rms` and `playback_rms` as lock-free atomics in `audio/engine.rs`), no
playback progress, no word/phoneme timing. `VariantProps.botStream/localStream`
are dead MediaStream props from the WebRTC era — nothing native populates
them. RMS-only is the biggest ceiling on "audio-driven shader animations."

### Persistence / gating / import surfaces (sidecar + shell)

- `orb:` block in `orbis.yaml` (`variant/palette/params/state_overrides/
  mood_overrides`), validated by `agent/config_store.py:216-274`; writes are
  atomic temp+rename. **POST `/api/config` with an `orb` block 403s without
  the customization entitlement** (`app.py:3889-3898` →
  `agent/entitlement.py:has_customization`).
- Precedents we can copy: **delegates** persist in app-data
  (`DELEGATES_YAML` env resolved by the Tauri shell to
  `app_data_dir()`, `src-tauri/src/lib.rs:1750-1795`) with CRUD API +
  `/api/delegates/reload` hot-swap; **widgets** are a single-source YAML
  catalog (`config/widgets.yaml`, PR #456) with a test guard keeping
  frontend folders and catalog ids in lock-step; **starter orbs**
  (`config/starter_orbs.yaml`) are already "orb config as data."
- No file-import surface exists yet: no Tauri dialog/fs plugin enabled, no
  multipart endpoints (python-multipart is installed but unused).

### Deploy surface for the editor

- `sites/marketing/` — Astro 5.7 + React islands, **same three 0.184 /
  @react-three/fiber 9.6 / drei 10.7 / vite-plugin-glsl stack as `web/`**,
  deployed by `.github/workflows/marketing-deploy.yml` to Cloudflare Pages
  (`orbis-marketing` project) at orbis.protolabs.studio; VitePress docs are
  built separately and **copied into `dist/docs/` before a single
  `wrangler pages deploy`** — the exact pattern an `/editor` SPA can reuse.
- The marketing site already **duplicates** the fractal variant
  (`sites/marketing/src/orb/`) with stubbed audio — evidence that a shared
  package is overdue (a third copy for the editor would be the tripwire).
- No workspace/monorepo today; `web/` and `sites/marketing/` are fully
  independent bun roots.

---

## 2. Direction — three moves

1. **`packages/orb-runtime`** — extract the portable orb core into a shared
   package and add a **generic, definition-driven rendering engine** to it.
2. **`.orbis` format + runtime import in the app** — a JSON definition the
   engine renders; import via drag-drop/file-picker; persisted in app-data;
   served + validated by the sidecar; gated behind the customization
   entitlement.
3. **`sites/editor/`** — a standalone Vite+React SPA, deployed into the
   existing marketing bundle at `/editor`, using the *same*
   `packages/orb-runtime` engine so what you see in the editor is what
   ORBIS renders.

A note on the locked direction: the no-browser rule (DECISIONS.md
2026-04-28) applies to the **ORBIS app runtime** (no PWA voice client, no
`getUserMedia` in the product). The editor is a **website tool**, like the
marketing site — browser APIs (including optional mic preview via Web
Audio) are fine there and never ship in the app.

---

## 3. The `.orbis` format (v1)

Prior art: **ISF (Interactive Shader Format)** — JSON header + GLSL, typed
INPUTS — and MilkDrop/Butterchurn for audio-reactive presets. We follow the
same shape, specialized to ORBIS's signal model.

Single-file JSON (UTF-8, `.orbis` extension). Zip container with textures /
multi-pass is deferred to v2 — the `format`/`version` fields exist so v1
files stay loadable forever.

```jsonc
{
  "format": "orbis-orb",
  "version": 1,
  "id": "aurora-veil",              // slug; collision-checked on import
  "name": "Aurora Veil",
  "author": "josh",
  "description": "ribbons of cold light",

  "engine": "raymarch-v1",          // which runtime renders it (see §4)
  "geometry": "sphere",             // "sphere" (orb SDF) | "quad" (fullscreen)

  "shaders": {
    "fragment": "/* GLSL — gets the standard prelude injected */",
    "vertex": null                   // null → engine default
  },

  "uniforms": {                      // declared, typed, defaulted
    "uDensity":  { "type": "float", "default": 2.0 },
    "uGlow":     { "type": "float", "default": 0.6 },
    "uPrimaryColor":   { "type": "color", "default": "#9b87f2" },
    "uSecondaryColor": { "type": "color", "default": "#6366f1" }
  },

  "fields": [                        // EXACT FieldSpec shape from field-types.ts
    { "kind": "color",  "key": "primaryEnergy", "label": "Primary",  "section": "color" },
    { "kind": "slider", "key": "density", "label": "Density", "section": "energy",
      "min": 0.2, "max": 4, "step": 0.05 }
  ],

  "palettes": {                      // same shape as presets.ts today
    "Aurora": { "primaryEnergy": "#7df9ff", "density": 2.2, "glow": 0.8 }
  },
  "defaultPalette": "Aurora",

  "bindings": [                      // declarative signal → uniform wiring
    { "signal": "time",      "target": "uTime",   "op": "set" },
    { "signal": "bot.level", "target": "uGlow",   "op": "add", "scale": 0.5 },
    { "signal": "user.level","target": "uDensity","op": "add", "scale": 1.2 },
    { "signal": "bot.band.2","target": "uShimmer","op": "add", "scale": 0.8,
      "smooth": 0.15 }               // optional one-pole smoothing
  ],

  "stateStyles": null,               // null → shared StateSnapshot defaults;
                                     // else per-state multiplier overrides
  "moodDefaults": {                  // same MoodOverrides shape as today
    "arousal": { "speed": 0.4 }
  },

  "post": { "bloom": { "intensity": 1.0, "threshold": 0.15 } }  // optional, declarative
}
```

**Bindings, not scripts.** v1 bindings are `{signal, target, op(set|add|mul),
scale, offset, smooth, curve(lin|exp|smoothstep)}` tuples — expressive enough
for every mapping the 12 hand-written variants do today, and **no JS
execution**, which is the entire security story: a `.orbis` file can contain
GLSL (GPU-sandboxed; worst case is a slow shader, mitigated by compile
validation + a frame-time watchdog that falls back to the default orb) and
JSON data, never runnable script. A tiny expression language is a possible
v2 if tuples prove limiting.

**Signal namespace (v1):** `time`, `bot.level`, `user.level`, `breath`,
`mood.valence|arousal|guardedness`, `pointer.clickStrength|dragVel`,
`state.<idle|listening|thinking|speaking>` (crossfaded 0–1 weights — strictly
more expressive than today's baked snapshot multipliers), and — once Phase 2
lands FFT — `bot.band.0-7` / `user.band.0-7`.

**Shader contract:** the engine injects a standard prelude (uniform decls
generated from `uniforms`, plus `uTime`, `uResolution`, camera/click
uniforms) so authors write the body against a documented contract — same
move ISF makes. Existing variant GLSL ports with minor edits.

---

## 4. App-side changes (ORBIS)

1. **Generic engine component** (`packages/orb-runtime`): a single
   `DefinitionVariant` React component that takes a parsed + validated
   `OrbDefinition` and does what every raymarch variant's 130 lines do
   today: build a `THREE.ShaderMaterial` from declared uniforms, run the
   shared hooks (crossfade, envelopes, breath, pointer), then apply
   `bindings` per frame. **Engines are versioned** (`raymarch-v1` first;
   `particles-v1` later for the instanced-mesh family; bespoke variants
   like galaxy/edison keep their hand-written components forever if we
   want).
2. **Loading + registration**: on boot (and on import), each definition is
   wrapped into a normal `VariantSpec` (`Component` = engine bound to the
   definition, `fields`/`palettes`/`moodDefaults` straight from the file)
   and pushed through the existing `variantRegistry.register()`. The
   registry's `subscribe()` means the picker/settings update live — **no
   changes to the settings panel, presets, randomize, or custom-preset
   storage; they're already schema-driven.**
3. **Persistence + API (sidecar)** — copy the delegates pattern:
   - Files live in app-data: `<app_data_dir>/orbs/<id>.orbis`; Tauri shell
     resolves and passes `ORBIS_ORBS_DIR` (like `DELEGATES_YAML`,
     `lib.rs:1750-1795`).
   - Sidecar: `GET /api/orbs` (catalog), `POST /api/orbs` (JSON body =
     the definition; validate schema, size cap ~512KB, id collision, GLSL
     string sanity), `DELETE /api/orbs/{id}`, all auth-gated. Validation
     mirrors `config_store._validate_orb()` style.
   - Frontend fetches the catalog at boot, registers each definition.
4. **Import UX**: enable the Tauri **dialog** plugin (file picker in the
   Orb tab) + window **drag-drop** of a `.orbis` file. Show shader
   compile errors inline before accepting (compile in a hidden context;
   reject on failure so a broken orb can't brick the stage).
5. **Entitlement**: importing/selecting a custom orb is customization →
   gate `POST /api/orbs` behind `has_customization()`, exactly like the
   `orb` config block (403 path at `app.py:3889`). Free tier keeps the
   starter pool. *(Open question §8.1 — could also be a separate
   `feat` in the license token.)*
6. **Cleanup riding along**: drop the dead `botStream`/`localStream`
   MediaStream props from `VariantProps` in the extracted runtime; the
   engine consumes a `signals` object instead. Built-in variants migrate
   incrementally (§6).

---

## 5. The editor app (`sites/editor/` → orbis.protolabs.studio/editor)

**Stack:** standalone Vite + React SPA (`base: '/editor/'`) — not an Astro
island; it's a full app (panes, state, code editor) and SPA tooling fits
better. Same React 19 / three 0.184 / R3F 9.6 / tailwind 4 versions already
used by both `web/` and `sites/marketing/`.

**Deploy:** extend `marketing-deploy.yml` exactly like the docs step — build
`sites/editor/`, copy `dist/` into `sites/marketing/dist/editor/`, same
single `wrangler pages deploy`. Trigger paths gain `sites/editor/**` and
`packages/orb-runtime/**`.

**Layout (v1):**

```
┌────────────────────────┬──────────────────────────────┐
│  Live preview          │  Tabs:                       │
│  (DefinitionVariant,   │   Shader   — CodeMirror 6,   │
│   same engine as app)  │             GLSL, inline      │
│                        │             compile errors    │
│  Signal simulator bar: │   Controls — field/uniform    │
│   state ▸ idle/listen/ │             editor (add       │
│   think/speak          │             slider/color →    │
│   bot/user level: auto │             uniform + field)  │
│   sine ▸ mic ▸ audio   │   Palettes — palette CRUD     │
│   file                 │   Bindings — signal→uniform   │
│   mood sliders         │             mapping table     │
│                        │   Meta     — name/author/desc │
│  [Import] [Export .orbis]                              │
└────────────────────────┴──────────────────────────────┘
```

- **Signal simulator** is the heart: scrub voice state (drives the real
  crossfade), synth envelopes (sine/pulse), **drop in an audio file** or
  use the browser mic (Web Audio `AnalyserNode` — gives the editor real
  RMS *and* FFT bands today, ahead of the app), pin mood values. This
  doubles as the long-missing mood-authoring surface (HANDOFF open
  question #2).
- **Templates**: "start from" gallery seeded with the ported built-ins
  (fractal/spectrum/nebula as `.orbis` templates) — also serves as living
  documentation of the format.
- **Export** = serialize + download `.orbis`. **Import** = round-trip.
  v1 is fully client-side (no backend, no accounts); localStorage drafts.

**Code sharing:** introduce a minimal **bun workspace** at the repo root
with `packages/orb-runtime` (engine, shared hooks minus voice/mood store
subscriptions, `compose.ts`, registry types, the `OrbDefinition`
zod schema). Per the portability audit, the entanglements to cut are
exactly four: `voiceStore` (inject `voiceState` as prop — already the
variant contract), `moodStore` (inject), Tauri `invoke` (only in
`OrbStage`, stays app-side), `@/lib/api` (stays app-side).
`sites/editor/` consumes the package from day one; `web/` adopts it for
the new engine immediately and migrates built-in variants gradually;
`sites/marketing/` can drop its duplicated orb copy later. If workspace
wiring fights the release/pyapp build, fallback is a `file:` dependency —
decided in Phase 0, not load-bearing for the design.

---

## 6. Built-in migration + experience upgrades

- **Port order** (proves the engine, then dogfoods it): spectrum →
  nebula → crystal → fractal (raymarch family). Particles/galaxy/edison/
  flux stay code variants until a `particles-v1` / layered engine exists
  — **no forced big-bang**; VariantSpec is the common denominator so code
  and data variants coexist indefinitely.
- **FFT bands (Phase 2, the real "audio-driven" unlock):** compute an
  8-band spectrum in the Rust engine for both mic and playback (rustfft or
  Goertzel over the existing ring buffers, stored like the RMS atomics),
  expose `get_audio_spectrum`, extend `useAudioEnvelopes` → signals
  `bot.band.N` / `user.band.N`. The editor simulates the same bands from
  Web Audio so authored orbs behave identically in-app.
- **Mood goes visible:** ported definitions ship `moodDefaults`, finally
  cashing in the dormant `composeBase` wiring (STATUS.md's "biggest
  visible gap").
- **Watchdog:** frame-time guard around custom orbs — sustained >50ms
  frames triggers fallback to the default starter + a toast, so a heavy
  imported shader can't wedge the app.

## 7. Phasing

**Phase 0 — runtime foundation — DONE 2026-06-10 (#496, #498)**
`packages/orb-runtime` + workspace; `OrbDefinition` zod schema +
versioned validation; `raymarch-v1` engine; spectrum ported as the proof;
`.orbis` import (dialog + drag-drop, `ORBIS_ORBS_DIR`, sidecar CRUD,
entitlement gate); registry runtime registration verified end-to-end.
*Exit: a hand-written `.orbis` file imports live and renders identically
to the built-in it was ported from.*

**Phase 1 — editor MVP — DONE 2026-06-10 (#499)**
`sites/editor/` SPA: preview + signal simulator + shader/controls/palette
/bindings tabs + export/import + 3 templates; `marketing-deploy.yml`
merge step; docs page for the format.
*Exit: author an orb at /editor in the browser, export, drag into ORBIS,
it renders.*

**Phase 2 — audio depth + polish**
Rust FFT bands + `get_audio_spectrum`; editor band simulation via Web
Audio; mood defaults on ported variants; remaining raymarch ports;
watchdog; format docs hardening.

**Phase 3 — later**
Zip container (textures, multi-pass), `particles-v1` engine, marketing
site consuming the shared package, community gallery / sharing, paid orb
packs distributed as `.orbis` files (note: signed `.orbis` files could
become the premium-orb distribution channel — packs without app releases).

## 8. Open questions (Josh's call)

1. **Gating:** custom-orb import behind the existing `customization`
   entitlement (recommended — it *is* customization), or free-to-import
   so `.orbis` becomes a viral surface while only the in-app *editor tab*
   stays paid? Affects paywall story; revisit against the go-live runbook.
2. **Premium built-ins as `.orbis`:** once the engine can express them,
   premium orbs could ship as signed definition files (Ed25519, same
   `license.py` machinery) instead of compiled-in variants — bigger
   refactor, real distribution upside. Phase 3 decision.
3. **Workspace vs `file:` dep** for `packages/orb-runtime` — Phase 0
   spike decides; check `release.yml`/pyapp build compatibility first.
4. **Editor branding:** orb-editor only, or name it for the broader
   "audio-driven shader animations" ambition (future widget/scene
   shaders)? Naming only — architecture is the same.
