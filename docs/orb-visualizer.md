# Orb visualizer (web plugin)

The orb is a self-contained plugin under `web/src/plugins/orb/`. It owns the Three.js scene, the audio-reactive pipeline, and the variant system — everything you see when protoVoice is on screen that isn't part of the drawer or the status pill.

## Why it's a plugin

protoVoice's web frontend uses a plugin + UI-slot registry (`plugins/PluginHost.tsx`, `plugins/registry.ts`). Each plugin registers at module import with a set of `ComponentType`s mounted in named slots (`stage`, `overlay-top`, `overlay-bottom`, `drawer-voice`, `drawer-orb`). The orb plugin contributes to `stage` (and an optional `OrbPreview` mounted by the drawer on mobile). No other plugin knows or cares how the orb is rendered.

This means you can swap the orb for a different visualizer (e.g. a 2D waveform, an Aura fragment-shader plane, a static hero image) by replacing `plugins/orb/` — the rest of the app keeps working.

## Directory layout

```
web/src/plugins/orb/
├── index.tsx                   registerPlugin({ id: 'orb', slots: { stage: OrbStage } })
├── OrbStage.tsx                primary <Canvas> + variant dispatcher + connect gesture + CA post
├── OrbPreview.tsx              secondary <Canvas> for the mobile drawer (no audio pipe)
├── store.ts                    central state store (variantId + palette + params + epoch)
├── broadcast.ts                applyParam / applyPreset / setVariant / loadCustomPreset
├── useOrbState.ts              useOrbState + useActiveVariant hooks
├── storage.ts                  localStorage wrappers (contract matches v0.6 vanilla)
│
├── shared/                     pure, variant-agnostic modules
│   ├── constants.ts            envelope / breath / state / zoom / drag tunings
│   ├── math.ts                 lerp, clamp01, easeInOutCubic
│   ├── color.ts                withHSL
│   ├── envelope.ts             Envelope class + rmsFromAnalyser
│   ├── stateSnapshot.ts        StateSnapshot + stateSnapshot() + lerpSnapshot()
│   ├── chromaticAberration.ts  luma-masked CA as a postprocessing.Effect
│   └── field-types.ts          FieldSpec / SECTIONS / random + format helpers
│
└── variants/
    ├── registry.ts             VariantSpec + VariantProps + registerVariant
    ├── index.ts                side-effect imports of built-in variants
    └── fractal/                the default variant (raymarched fractal)
        ├── index.tsx           registerVariant({ id: 'fractal', ... })
        ├── FractalVariant.tsx  driver + scene graph
        ├── materials.ts        drei shaderMaterial() factories + extend()
        ├── presets.ts          palettes (Aurora / Ember / Citrus / Forest / Noir)
        ├── schema.ts           FIELDS consumed by settings panel
        └── shaders/            .glsl files, HMR via vite-plugin-glsl
```

## The shared signal bus

Every variant receives the same three signals. This is deliberate — matches the industry pattern (ElevenLabs Orb, Vapi, LiveKit Aura, orb‑ui all expose the same shape). Variants differ in **how** they interpret the signals.

### 1. Voice state (from RTVI)

`idle / listening / thinking / speaking`, driven by `VoiceStateBridge` subscribing to the pipecat client's events. Translated into a `StateSnapshot` (primary + secondary colors, density, glow, scale, rotation, speed, chromatic aberration) by `stateSnapshot()`. Per-state values are tuned in `shared/stateSnapshot.ts`:

| State | Shape cue | Saturation / luminance |
|---|---|---|
| `idle` | scale 0.94, slow rotation, low density | –20% saturation, –30% luminance |
| `listening` | scale 0.93 (inward pull) | –5% saturation, –5% luminance |
| `thinking` | scale 0.96, faster internal swirl | –5% saturation, –10% luminance |
| `speaking` | scale 1.06 (outward push), full intensity | full saturation, full luminance |

Crossfades run for `STATE_XFADE_MS` (600 ms default) with `easeInOutCubic`.

### 2. Audio envelopes

Two asymmetric two-stage envelope followers (`shared/envelope.ts`) driven by `MediaStream`s from the pipecat client (`usePipecatClientMediaTrack('audio', 'bot' | 'local')`). The followers give fast attack / slow release so speech gaps don't flip the state machine on every breath.

Normalized to `[0, 1]` via `NORM_FLOOR` / `NORM_CEIL` and smoothed a third time with `DISP_ALPHA` before the final uniform hit. Exposed to variants as `dBot` and `dUser` refs.

### 3. Idle breath

Two non-commensurate sines at ~0.10 Hz and ~0.037 Hz sum to a life-without-loops modulation. Amplitude `BREATH_AMP` (default 3% of state scale).

### Gestures (free to every variant)

- **Drag-to-spin** — pointer events on the canvas rotate the orb mesh with momentum damping (`DRAG_DAMP`, `DRAG_VEL_MAX`).
- **Click-pulse** — raycast on the mesh → `uClickDir` + `uClickStrength` refs spike to 1 and decay (`CLICK_DECAY`). Single-tap-to-connect on touch is a separate gesture handled at `OrbStage`.
- **Wheel-zoom** — camera Z lerped between `ZOOM_MIN` and `ZOOM_MAX` (6 → 20).

## Variant system

### VariantSpec

```ts
export interface VariantSpec {
  id: string;                              // unique, persisted to localStorage
  name: string;                            // shown in the picker
  description?: string;
  Component: ComponentType<VariantProps>;  // the R3F scene graph
  palettes: Record<string, Record<string, unknown>>;
  fields: FieldSpec[];                     // consumed by the settings panel
  defaultPalette: string;
}

export interface VariantProps {
  voiceState: VoiceState;
  botStream?: MediaStream | null;
  localStream?: MediaStream | null;
}
```

### Adding a variant

1. Create `plugins/orb/variants/<id>/index.tsx`:
   ```ts
   import { registerVariant } from '../registry';
   import { MyVariant } from './MyVariant';
   import { MY_FIELDS } from './schema';
   import { MY_PRESETS } from './presets';

   registerVariant({
     id: 'my-variant',
     name: 'My variant',
     Component: MyVariant,
     palettes: MY_PRESETS,
     fields: MY_FIELDS,
     defaultPalette: 'Aurora',
   });
   ```
2. Add `import './my-variant';` to `variants/index.ts`.
3. That's it. The settings panel auto-renders the picker when a second variant registers, pulls `fields` + `palettes` from the registry, and the preview mirrors the selection on mobile.

### Inside a variant component

Minimum loop: subscribe to the store, run `useFrame` to read current params, write to shader uniforms / mesh properties. See `FractalVariant.tsx` for a fully-specced implementation (state crossfade + audio envelopes + drag + pulse + zoom). The long-term shape is for the shared driver to move into hooks (`useOrbDriver`, `useStateTransition`, `useAudioEnvelopes`, `usePointerInteraction`) so new variants are ~80 LOC instead of ~500.

## Store + broadcast

```
settings panel
     │
     │  applyParam / applyPreset / setVariant / loadCustomPreset
     ▼
  broadcast.ts ─► orbStore  ─► useSyncExternalStore  ─► active variant
                                                     └► orb preview
```

The store is a plain external store (no Zustand / Jotai dependency). Re-renders only when `epoch` ticks; `useVoiceStateSelector` is the perf-sensitive read pattern if a component only needs one slice.

Caching rule: any collection returned to `useSyncExternalStore` must be a stable reference until actually changed. `variantRegistry.all()` violating that rule was the cause of the React #185 infinite-loop bug fixed in v0.8.0.

## localStorage contract

Keys are stable across vanilla → R3F migration, so saved user data carries over:

| Key | Shape |
|---|---|
| `protoVoice.orb.variant` | `string` — active variant id |
| `protoVoice.palette`     | `string` — active palette name for the current variant |
| `protoVoice.params`      | `Record<string, unknown>` — current param overrides |
| `protoVoice.customPresets` | `Record<string, { palette, params }>` — user-saved presets |
| `protoVoice.tab`         | `'voice' \| 'orb'` — last-opened drawer tab |

## Industry references

The shared-signal shape matches:

- **ElevenLabs Orb** — `getInputVolume()` / `getOutputVolume()` = our dBot / dUser. 4 states: `idle / thinking / listening / talking`.
- **Vapi 3D Orb** — morphing IcosahedronGeometry + simplex noise, driven by `volumeLevel`.
- **LiveKit Aura** — GLSL fragment-shader pulse field, `colorShift` param, per-state animations.
- **orb-ui** (alexanderqchen) — open-source, Vapi/ElevenLabs adapters, same 4-state API.

See `docs/explanation/browser-first-inference.md` for the research trail on other client-side architectural questions.

## Tunables reference

All constants live in `shared/constants.ts`. Changing them is a deliberate UX call.

| Constant | Default | Purpose |
|---|---|---|
| `ENV_USER`, `ENV_BOT` | `{attack: 0.22–0.25, release: 0.04–0.10}` | Asymmetric envelope — fast onset, slow decay |
| `ENV_STAGE2` | `0.22` | Second-stage EMA smoothing |
| `DISP_ALPHA` | `0.10` | Final display smoothing on envelope |
| `NORM_FLOOR` / `NORM_CEIL` | `0.020` / `0.300` | Byte-domain RMS normalization range |
| `SPEAK_ENTER` / `SPEAK_EXIT` | `0.08` / `0.035` | State-machine hysteresis |
| `LISTEN_MIN_DWELL_MS` | `500` | Min dwell in listening (syllable gaps) |
| `THINK_DWELL_MS` | `1400` | Handoff beat after user stops |
| `STATE_XFADE_MS` | `600` | Crossfade duration |
| `MAX_DELTA_S` | `1/30` | Per-frame delta cap |
| `BREATH_HZ_1` / `BREATH_HZ_2` | `0.10` / `0.037` | Idle-breath sine frequencies |
| `BREATH_AMP` | `0.03` | Fraction of scale to modulate |
| `ROTATION_SCALE` | `0.45` | Global auto-rotation damping |
| `DRAG_VEL_MAX` | `3.5` | Max drag momentum (rad/s) |
| `DRAG_DAMP` | `0.96` | Per-frame drag decay |
| `CLICK_DECAY` | `0.93` | Per-frame click-bloom decay |
| `ZOOM_MIN` / `ZOOM_MAX` | `6` / `20` | Camera-Z clamp |
| `ZOOM_LERP` | `0.15` | Wheel-to-target lerp per frame |

## Known pitfalls

- **Never drive shader uniforms via React props that change every frame.** `<fractalMaterial uDensity={density} />` with `density` from useState = reconcile-per-frame = catastrophic. Always write to `matRef.current.uniforms.uX.value` inside `useFrame`.
- **`useSyncExternalStore` snapshots must be reference-stable** until actually changed. A fresh `Array.from(...)` on every read causes React #185.
- **iOS Safari is strict about AudioContext creation** — must follow a user gesture. The tap/dblclick-to-connect handlers on `OrbStage` double as the gesture that resumes the audio context.
- **StrictMode double-invokes effects in dev.** VoiceOrb (pre‑R3F) used a `cleaned` sentinel; R3F's declarative `<Canvas>` sidesteps this, but any imperative Three.js you add must guard.
- **Wrap `uTime` at 2π·N** (`TIME_WRAP`) — float32 precision in GLSL degrades visibly after ~10 minutes of runtime.
