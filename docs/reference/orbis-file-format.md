# The `.orbis` file format

A `.orbis` file is one JSON document describing a complete orb: GLSL shader,
typed uniforms, settings controls, palettes, and **bindings** that wire live
signals (voice levels, voice state, mood, time) into uniforms. ORBIS renders
it with the `raymarch-v1` engine — the same engine behind the
[orb editor](https://orbis.protolabs.studio/editor/)'s preview, so an orb that
looks right in the editor looks right in the app. Authoring walkthrough:
[Create custom orbs](/how-to/create-custom-orbs).

There is **no executable code in the format besides GLSL** (which runs on the
GPU). Bindings are data, not scripts — that's what makes importing a stranger's
`.orbis` file reasonable.

## Top level

```jsonc
{
  "format": "orbis-orb",        // required, exactly this
  "version": 1,                  // required, exactly 1
  "id": "aurora-veil",          // slug [a-z0-9-], 2–64 chars; the registry key
  "name": "Aurora Veil",        // ≤120 chars
  "author": "you",              // optional
  "description": "…",            // optional
  "engine": "raymarch-v1",      // required; the only v1 engine

  "geometry": { "type": "sphere", "radius": 5.5 },          // optional
  "material": { "blending": "additive" },                    // optional
  "motion":   { "scaleAudioPump": 0.06, "breath": true },    // optional

  "shaders":  { "fragment": "…GLSL…", "vertex": null },
  "uniforms": { … },
  "fields":   [ … ],
  "palettes": { … },
  "defaultPalette": "Aurora",
  "bindings": [ … ],
  "moodDefaults": { … },        // optional
  "premium": true,               // optional; imported orbs default to true
  "post": null                   // optional; { "bloom": { "intensity": 1.0 } }
}
```

Limits: definition ≤ 512 KB, fragment ≤ 256 KB, ≤ 64 uniforms, ≤ 64 fields,
≤ 32 palettes, ≤ 128 bindings.

## The GLSL contract

`shaders.fragment` is the fragment **body** — ORBIS injects a prelude above it
containing the standard uniforms, the varyings, and a declaration for every
uniform you declare:

```glsl
uniform float uTime;            // engine-managed: speed-scaled, wrapped
uniform vec3  uLocalCamPos;     // camera position in the orb's local space
uniform vec3  uPrimaryColor;    // voice-state-shaded palette colors —
uniform vec3  uSecondaryColor;  //   set from the state snapshot unless you bind them
uniform vec3  uClickDir;        // last click direction (local space)
uniform float uClickStrength;   // click bloom, decays per frame
varying vec3 vLocalPosition;    // this fragment's position on the sphere shell
varying vec3 vNormal;
varying vec3 vViewPosition;
```

The mesh is a sphere shell (default radius 5.5; camera at z = 13). The
raymarch convention used by every built-in: ray origin `uLocalCamPos`, ray
direction `normalize(vLocalPosition - uLocalCamPos)`. Mesh rotation (auto +
drag) is handled by the engine, so your shader works in local space and gets
rotation for free. Write `gl_FragColor`; additive blending with
luminance-derived alpha is the house style.

## `uniforms`

```jsonc
"uniforms": {
  "uGlow":   { "type": "float", "default": 1.0 },
  "uPhases": { "type": "vec4",  "default": [5.8, 4.1, 2.8, 0.2] },
  "uTint":   { "type": "color", "default": "#9b87f2" }   // color = vec3 in GLSL
}
```

Types: `float`, `vec2`, `vec3`, `vec4`, `color`. Names must match
`u[A-Za-z0-9_]+` and may not shadow the standard uniforms.

## `fields` and `palettes`

`fields` is the schema the ORBIS settings panel renders — identical to the
built-in orbs':

```jsonc
{ "kind": "slider", "key": "glow", "label": "Glow",
  "section": "energy", "min": 0.1, "max": 2.5, "step": 0.05 }
{ "kind": "color", "key": "primaryEnergy", "label": "Primary", "section": "color" }
```

Sections: `color`, `energy`, `motion`, `fractal`, `perf`. A few keys are
load-bearing because the **voice-state snapshot** reads them: `density`,
`atmosphereGlow`, `speed`, `chromaticAberration`, `asymmetry`, `orbRotation`,
`primaryEnergy`, `secondaryEnergy` (plus `dpr` for resolution). Missing ones
fall back to sane defaults.

`palettes` maps a name to a full set of param values; `defaultPalette` must
name one of them. Users can still save their own presets on top, like any orb.

## `bindings`

The reactivity model. Each binding is a tuple:

```jsonc
{ "target": "uGlow", "signal": "bot.level", "op": "add",
  "scale": 0.5, "offset": 0, "curve": "linear", "smooth": 0.15 }
```

Per target, bindings evaluate **top-down every frame**, starting from the
uniform's declared default:

```
value = curve(signal) * scale + offset      // curve: linear | exp | smoothstep
acc   = acc <op> value                       // op: set | add | mul
```

`smooth` (0–1] applies one-pole smoothing to the value before the op. Vector
components bind individually: `"target": "uPhases.x"`. Color targets
(`uPrimaryColor`, `uSecondaryColor`, or `color` uniforms) accept color signals
only, with op `set`. `uTime`, `uLocalCamPos`, and `uClickDir` are
engine-managed and cannot be bound.

### Signals

| Signal | Range | What it is |
|---|---|---|
| `time` | seconds | engine time, scaled by the state-snapshot speed |
| `bot.level` | 0–1 | her voice — live TTS level, envelope-smoothed |
| `user.level` | 0–1 | your voice — live mic level, envelope-smoothed |
| `breath` | ±0.75 | idle-breath sine pair |
| `pointer.clickStrength` | 0–1 | click bloom, decaying |
| `mood.valence` / `mood.arousal` | −1–1 | the companion's mood |
| `mood.guardedness` | 0–1 | |
| `snap.density` `snap.glow` `snap.speed` `snap.ca` `snap.asymmetry` `snap.rotation` `snap.scale` | — | the crossfaded voice-state snapshot (idle/listening/thinking/speaking) |
| `snap.primary` / `snap.secondary` | color | state-shaded palette colors |
| `param.<key>` | — | any settings param (number → scalar, color string → color) |

## `moodDefaults`

Per-dimension deltas applied to **params** (not uniforms), scaled by the live
mood value — same mechanism as the built-in orbs:

```jsonc
"moodDefaults": {
  "arousal": { "speed": 0.4, "glow": 0.15 },
  "guardedness": { "glow": -0.15 }
}
```

## Engine extras

- `motion.scaleAudioPump` — mesh-scale pump from `bot.level` (default 0.06).
- `motion.breath` — idle-breath scale modulation (default true).
- `motion.rotationXRatio` — x-axis auto-rotation as a ratio of y (default 0.5).
- `material.blending` — `additive` (default) or `normal`; plus `transparent`,
  `depthWrite`, `doubleSide`.
- `post.bloom` — optional bloom pass: `intensity`, `luminanceThreshold`,
  `luminanceSmoothing`, `mipmapBlur`.

## A word on safety

Validation (structure, caps, binding targets/signals) runs both in the editor
and in ORBIS at import; the shader is test-compiled against a real GL context
before the orb is accepted. The worst a hostile file can be is an ugly or slow
shader — there is no scripting surface.
