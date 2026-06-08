# Plugins

A **plugin** is always-on chrome: a button, an overlay, a drawer tab — anything
that contributes UI to a fixed region of the app. (Openable *content* with its
own surface is a [widget](../widgets/README.md), not a plugin.)

Plugins are **auto-discovered**. Every `plugins/<name>/index.tsx` that calls
`registerPlugin()` at module top-level is wired in by the eager glob in
`plugins/index.ts` — there is no import list in `App.tsx` to edit.

## Add one

Create `web/src/plugins/<name>/index.tsx`:

```tsx
import { registerPlugin } from '@/sdk';
import { MyThing } from './MyThing';

registerPlugin({
  id: 'my-thing',                       // unique, kebab-case
  slots: { 'overlay-top': MyThing },    // where it renders
});
```

That's the whole registration. Drop the folder in; it loads.

## Slots

Pick the region your component renders into (`UISlotName` in
[`registry.ts`](./registry.ts)):

| Slot | Region |
| --- | --- |
| `stage` | primary visual area (the orb) |
| `overlay-top` | floating top — status/trace chips, the reminders bell |
| `overlay-bottom` | floating bottom — the status pill, transcript strip |
| `drawer-quick` | drawer tab: at-a-glance status + most-used toggles (landing) |
| `drawer-voice` | drawer tab: audio I/O (mic, activation, STT, TTS) |
| `drawer-brain` | drawer tab: LLM, delegates, replies |
| `drawer-settings` | drawer tab: access, setup, diagnostics, about |
| `drawer-dev` | drawer tab: developer observability |

One plugin can contribute to several slots: `slots: { 'overlay-top': Bell, 'drawer-brain': BellSettings }`.

A plugin with no UI (`slots: {}`) is valid — use it for a side-effecting driver
that just needs to run on load (see `mood/`).

## Ordering

When several plugins share a slot (today only `overlay-top`), render order comes
from the optional `order` field (lower = earlier; default 100), **not** import
order:

```tsx
registerPlugin({ id: 'my-thing', order: 25, slots: { 'overlay-top': MyThing } });
```

## What you can import

The blessed surface is **`@/sdk`** — the registration functions, their types,
the voice-state hooks (`useVoiceState`/`useVoiceStateSelector`), and
`pushStatusTransient`. Beyond that, these shared modules are fair game:

- the backend client — `@/lib/api`
- UI primitives + tokens — `@/components/ui/*`, the design tokens in `@/index.css`

**Don't** reach into another plugin's folder for its internals — if you need
something shared, lift it to `@/shared` (or re-export it from `@/sdk`).

See [CONTRIBUTING.md](../../../CONTRIBUTING.md) for the big picture.
