# Create custom orbs (the orb editor)

ORBIS can load **orbs you author yourself** — shader, palettes, controls, and
audio reactivity — from a single `.orbis` file. You build one in the
**[orb editor](https://orbis.protolabs.studio/editor/)**, a browser app with a
live preview that uses the *same* rendering engine as ORBIS, then import the
file in the app. For every field in the format, see the
[.orbis file format reference](/reference/orbis-file-format); for tweaking the
built-in orbs instead, see [Customize your orb](/how-to/customize-your-orb).

## Before you start

- Custom-orb import is part of the **paid customization unlock** (the same one
  that opens the orb editor tab in the app).
- The editor runs entirely in your browser — nothing you author is uploaded
  anywhere. Drafts autosave locally; **Export** is how you keep your work.

## Author an orb

1. Open **[orbis.protolabs.studio/editor](https://orbis.protolabs.studio/editor/)**.
2. Pick a starting point from **New from template…** — *My First Orb* is a
   fully-commented minimal shader; *Prism* is a real volumetric raymarch.
3. **Shader** tab — edit the GLSL fragment. The injected prelude (expand it at
   the top) gives you `uTime`, `uLocalCamPos`, the brand colors, click
   uniforms, and every uniform you declare. Your code compiles on each pause;
   errors show below while the last good shader keeps rendering.
4. **Controls** tab — *Add control* creates a uniform + settings field +
   palette entry + binding in one step. The sliders here are exactly what the
   ORBIS settings panel will show for your orb. Save the current values as a
   palette when you like them.
5. **Bindings** tab — wire live signals into uniforms (her voice level, your
   voice level, voice state, mood, time). See the
   [signals table](/reference/orbis-file-format#signals).
6. Use the **simulator bar** under the preview to test reactivity: scrub the
   voice state, set a level source to *pulse* or *manual* — or *mic* to drive
   it with your real microphone — and pin mood values.
7. **Export .orbis** downloads the file.

## Import into ORBIS

1. In ORBIS, open the drawer → **Orb** → the **Style** panel.
2. Click **Import .orbis** and pick your file. ORBIS validates it, compiles
   the shader, saves it to app data (it survives updates), and switches to it.
3. Imported orbs appear in the variant list tagged **Imported**. Select one
   and **Remove this orb** deletes it.

Re-importing a file with the same `id` replaces the previous version — the
iterate loop is: edit in the editor → export → import again.

## If the import is rejected

- **Validation errors** — the message lists what's wrong (the format reference
  documents every rule). Fix in the editor's **JSON** tab if it's structural.
- **“shader failed to compile”** — the GLSL didn't compile on your machine's
  GPU; the editor's Shader tab shows the same diagnostics.
- **“collides with a built-in orb”** — pick a different `id` in the **Meta**
  tab; only re-imports of *your own* imported orbs may reuse an id.
