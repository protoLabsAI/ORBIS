# The orb

ORBIS could have been a text box. It's an **orb** because a voice companion
should feel *present* — something you glance at and instantly read, the way you
read a face. This page explains what the orb is and why it's built the way it is.

For the controls, see the [Orb reference](/reference/orb).

## A face, not a chrome

The orb isn't decoration around the "real" app — it *is* the app's surface. It
reflects what ORBIS is doing at a glance:

- **Idle** — at rest, breathing slowly.
- **Listening** — engaged with your voice.
- **Thinking** — working on a turn (a tool call, a delegation).
- **Speaking** — voicing a reply.

It also reacts to sound — the orb moves with the audio envelope — so a reply
*looks* like it's being spoken, not just played.

## Variants and palettes

Two layers of customization:

- A **variant** is a whole rendering style — fractal, crystal, galaxy, nebula,
  and so on. Each is a self-contained visual with its own parameters.
- A **palette** is a named colour scheme within a variant. Every variant also
  carries the **ProtoLabs** palette — the brand lavender→indigo — so you can
  make any orb on-brand in one click.

From there, individual parameters (colour, energy, motion) let you make the orb
yours, and you can save the result as a preset.

## Why a plugin system

Each variant is an independent plugin over a shared signal bus: voice state,
audio envelopes, an idle "breath," and gestures are provided to every variant,
and each one decides how to express them. That's why there are eight very
different orbs that all react identically to your voice — and why new ones can
be added without touching the rest of the app.

## State, not just style

Because the orb is the status surface, its look can shift **per state** — subtly
brighter or more active while listening, for instance. The
[authoring context](/reference/orb#authoring-context) lets you tune those
state-specific looks separately from the base.

## See also

- [Orb reference](/reference/orb)
- [Customize your orb](/how-to/customize-your-orb)
- [How ORBIS works](/explanation/how-orbis-works)
