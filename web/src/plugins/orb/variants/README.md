# Orb variants

A **variant** is a visual style for the orb — its geometry, shader, palettes, and
how it reacts to voice and mood. `fractal`, `nebula`, `crystal`, and `particles`
are the free starters; the rest are premium.

Variants are **auto-discovered**. Every `variants/<id>/index.tsx` that calls
`registerVariant()` at module top-level is wired in by the eager glob in
`variants/index.ts` — there is no import list to edit.

## Folder layout

A variant is a folder; the convention (see `fractal/`) is:

```
variants/<id>/
  index.tsx          registerVariant({...})  ← the only required file
  <Name>Variant.tsx  the React component (renders the Three.js mesh)
  schema.ts          FIELDS — the settings the customize panel exposes
  presets.ts         named palettes (param presets)
  materials.ts       shader material setup            (optional)
  shaders/           .glsl                             (optional)
```

## Register it

`variants/<id>/index.tsx`:

```tsx
import { registerVariant } from '../registry';
import { MyVariant } from './MyVariant';
import { MY_PRESETS } from './presets';
import { MY_FIELDS } from './schema';

registerVariant({
  id: 'my-variant',                 // matches the folder name
  name: 'My Variant',
  description: 'One line shown in the picker.',
  Component: MyVariant,             // gets VariantProps (voiceState, streams)
  palettes: MY_PRESETS,
  fields: MY_FIELDS,                // drives the settings panel
  defaultPalette: 'Default',
  // moodDefaults: {...}            // optional mood→uniform reactions
  // premium: true                  // see below
});
```

`Component` receives `VariantProps` — `{ voiceState, botStream, localStream }` —
and should react to `voiceState` (idle/listening/thinking/speaking). The
`fields` schema is what the customize panel renders, so users can tune it.

## Free vs premium

- Omit `premium` → the variant is **free**. To put it in the first-run starter
  pool, add an entry to `config/starter_orbs.yaml` referencing its `id`.
- `premium: true` → registered like any other but reachable only through the
  paid customization editor; never in the free starter pool.

## Gotchas

Variant shaders are camera-space sensitive — the orb camera sits much further
back than a typical screen-space prototype, which inflates exponential glow.
Before porting a prototype shader, read the variant-system reference:
[docs/internal/orb-visualizer.md](../../../../../docs/internal/orb-visualizer.md).

See [CONTRIBUTING.md](../../../../../CONTRIBUTING.md) for the big picture.
