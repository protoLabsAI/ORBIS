# In-app orb editor → parity with the live editor (plan of record)

Status: **proposed** (2026-06-24). Tracking epic: see GitHub issues at the
bottom. Direction: **everything is free + open source; the orb editor is
unblocked** (no paywall).

## Goal

Make the **in-app** orb editor (inside the ORBIS app) as robust as the
standalone **live editor** at `orbis.protolabs.studio/editor/` — full authoring
(shader, uniforms, controls, bindings, meta, JSON) with live compile-check,
against the orb actually running in the app.

## Current state (gap)

| | Live editor (`sites/editor/`) | In-app editor (`web/src/plugins/orb-settings/`) |
| --- | --- | --- |
| Shader (GLSL) | ✅ CodeMirror + live `compileCheck` | ❌ |
| Declare uniforms / add control | ✅ one-click add-control | ❌ (variant schema is fixed) |
| Bindings (signal→uniform) | ✅ table editor | ❌ |
| Meta (id/name/motion/material) | ✅ | ❌ |
| Raw JSON edit | ✅ validated | ❌ |
| Palette / preset | snapshot | ✅ select + save/load custom presets |
| State/mood delta authoring | ❌ | ✅ (numeric) |
| Status | full authoring env | **preset + delta editor**, entitlement-gated, `orb` tab removed from the Drawer |

Both render through the **same** `DefinitionOrb` and use `@orbis/orb-runtime`
(`compileCheck`, `validateOrbDefinition`, `composeBase`, types). The app already
supports runtime `.orbis` import + `registerDefinition`. **No architectural
blocker** — it's a UI-extraction + wiring + ungate job.

Key files: live editor panes `sites/editor/src/panes/*` + store
`sites/editor/src/state.ts`; in-app `web/src/plugins/orb-settings/OrbSettingsPanel.tsx`
(gate `useEntitlement.ts`), Drawer tab gating `web/src/components/Drawer.tsx:19-26`,
orb store `web/src/plugins/orb/store.ts`, runtime import
`web/src/plugins/orb/definitions/runtime.ts`.

## Approach — extract the panes into a shared package

Recommended over the alternatives:

- **Extract (chosen):** lift the panes into a shared package as **controlled,
  stateless** components that take an `OrbDefinition` + callbacks (and
  `@orbis/orb-runtime` for compile/validate). `sites/editor` keeps its store and
  consumes them (zero UX change — proves the extraction); the app mounts the same
  panes against its orb. One codebase, both surfaces.
- *Embed the editor in an iframe/webview* — fastest 100% parity but duplicates
  CodeMirror + a second Three.js canvas; perf + integration cost. Rejected.
- *Rebuild in-app* — duplication + drift. Rejected.

## Phases (→ GitHub sub-issues)

- **P0 — Unblock & expose.** Remove the entitlement gate on orb editing; restore
  the `orb` tab in the Drawer (`TabName` + render the `drawer-orb` slot); drop
  paywall/"unlock" language from docs + UI (`UnlockCustomization`). Ships the
  *current* in-app editor to everyone, free.
- **P1 — Extract panes → shared package** (`packages/editor-ui`, name TBD).
  `ShaderPane` / `ControlsPane` / `BindingsPane` / `MetaPane` / `JsonPane` as
  controlled components on `@orbis/orb-runtime`. Refactor `sites/editor` to
  consume them with **no behavior change** (the proof the extraction is clean).
- **P2 — Robust in-app editor.** Mount the shared panes in the app's `orb` tab
  against a full `OrbDefinition`; wire to the orb store + runtime import/export +
  a live preview of the actual app orb; in-app file import + export. The `orb`
  tab becomes a real authoring surface (shader/controls/bindings/meta/json).
- **P3 — Unify authoring polish.** Port the in-app **state/mood delta**
  authoring (`FieldDeltaSlider` / `AuthoringContext`) into the shared controls so
  both editors get it; preview parity (voice-state/mood sim).

## Constraints / open questions

- `compileCheck` needs a real WebGL context — present in the app webview. ✅
- Hand-written **variants** stay immutable React components; full authoring
  targets **`.orbis` `OrbDefinition`s**. Open: offer "fork a variant into an
  editable `.orbis`" so users can start from a built-in look?
- CodeMirror bundle size in the app — lazy-load the editor panes (like the
  WebMCP chunk) so they don't weigh the boot path.
- Does the in-app editor eventually **replace** the standalone live editor, or
  do both stay (app = primary authoring, site = shareable/linkable)? Lean: both.
- Ties in: the WebMCP tool surface (#532) already drives the editor store; once
  panes are shared, the same tools could drive the in-app editor too.
