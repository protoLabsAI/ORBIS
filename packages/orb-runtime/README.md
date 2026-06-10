# @orbis/orb-runtime

The shared orb rendering runtime: the **`.orbis` definition format** (types +
validator), the **`raymarch-v1` engine** (`DefinitionOrb`) that renders a
definition, and the pure animation core (state snapshots, crossfade, compose
math, interaction hooks) that both the hand-written variants and the
data-driven engine build on.

Consumed **as source** via bundler alias — it is not installed from a
registry and has no dependencies of its own beyond the host app's
`three` / `@react-three/fiber` / `react`:

- `web/` (the ORBIS app): `vite.config.ts` aliases `@orbis/orb-runtime` →
  `../packages/orb-runtime/src`, and `tsconfig` mirrors it in `paths`.
- `sites/editor/` (the orb editor): same pattern.

Plan of record: `docs/internal/orb-format-and-editor.md`.

## The `.orbis` format, in one breath

A single JSON file: GLSL fragment body + typed uniform declarations +
settings-panel field schema + palettes + **declarative signal→uniform
bindings** (no executable JS). The engine injects a standard GLSL prelude
(`uTime`, `uLocalCamPos`, `uPrimaryColor`, `uSecondaryColor`, `uClickDir`,
`uClickStrength`, the sphere varyings, and every declared uniform); the
fragment body writes `gl_FragColor` from `vLocalPosition` raymarch
convention — the same contract every built-in raymarched variant uses.

Bindings are `{target, signal, op, scale, offset, curve, smooth}` tuples,
evaluated in order per frame from the uniform's declared default:
`value = curve(signal) * scale + offset`, then `acc = acc <op> value`.
Signals: `time`, `bot.level`, `user.level`, `breath`, `mood.*`,
`pointer.clickStrength`, `snap.*` (crossfaded voice-state snapshot),
`param.*` (composed settings params).
