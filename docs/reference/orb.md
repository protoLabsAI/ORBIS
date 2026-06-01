# The orb

The orb is the face of ORBIS — the living visual you talk to. It's fully
customizable in **Settings → Orb**. For the *why*, see [The orb](/explanation/the-orb).

## Variants

A **variant** is a different way of rendering the orb. ORBIS ships eight, each
with its own look and its own controls:

| Variant | Feel | Default palette |
| --- | --- | --- |
| **Fractal** | Volumetric ray-marched fractal with an atmosphere shell. | Aurora |
| **Crystal** | Faceted, refractive glass. | Prism |
| **Galaxy** | Plasma + particle field with a shell. | Andromeda |
| **Lattice** | Crystalline grid. | Glasshouse |
| **Nebula** | Soft volumetric cloud. | Andromeda |
| **Particles** | A cluster of points. | Constellation |
| **Spectrum** | Procedural rainbow. | Rainbow |
| **Tetra** | Folded geometric form. | Drift |

## Palettes

Each variant ships several **palettes** (named colour schemes), plus a
**ProtoLabs** palette — the protoLabs.studio brand scheme (lavender → indigo,
`#9b87f2 → #6366f1`) available on every variant.

Pick a variant first, then a palette; the palette sets the orb's colours and a
matching look. From there you can fine-tune individual parameters.

## Parameters

Selecting a variant exposes its controls, grouped into collapsible sections.
The common groups (exact controls vary per variant):

| Section | Controls |
| --- | --- |
| **Color** | Primary + secondary energy colours. |
| **Energy** | Density, glow, halo thickness & scale, chromatic aberration. |
| **Motion** | Internal speed, auto-rotation, animation speed. |
| *Variant-specific* | e.g. Fractal's iterations / scale / decay / smoothness. |
| **Perf** | Resolution (DPR) — lower it if the orb is heavy on your machine. |

## Authoring context

The Orb panel can edit either the **base** look or **per-state** overrides —
how the orb shifts when it's *listening*, *thinking*, or *speaking*. Switch the
authoring context to tune a specific state without changing the base.

## Presets

Save your tuned look as a named **preset** (scoped to the active variant), and
re-apply it later. **Randomize** rolls new parameters for inspiration (it leaves
your manually-set resolution alone).

## See also

- [Customize your orb](/how-to/customize-your-orb)
- [The orb](/explanation/the-orb)
