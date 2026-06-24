# Author orbs with an AI agent (WebMCP)

The [orb editor](https://orbis.protolabs.studio/editor/) exposes its authoring
tools to AI agents over **[WebMCP](https://webmachinelearning.github.io/webmcp/)**
— the emerging W3C standard that lets a web page hand an agent a set of typed,
callable tools instead of making it screenshot the page and guess where to
click. A connected agent can write the shader, declare uniforms, add controls,
wire audio/mood reactivity, and export the finished `.orbis` — and **every call
updates the live preview in real time**, because the agent drives the exact same
editor state your mouse does.

This is for building orbs *conversationally* ("give it a slow indigo pulse that
brightens when I speak"). To author by hand instead, see
[Create custom orbs](/how-to/create-custom-orbs).

## Connect an agent

The editor registers its tools on `navigator.modelContext` / `document.modelContext`
as soon as the page loads (it ships a polyfill, so you don't need a bleeding-edge
browser). You need a WebMCP **consumer** to call them. Pick one:

- **Test it manually** — install the *Model Context Tool Inspector* extension
  from the Chrome Web Store, open the editor, and the inspector lists every tool
  and lets you call it with arguments. Best way to see what's available and
  confirm the preview reacts.
- **Bridge to a desktop agent** — the [MCP-B](https://docs.mcp-b.ai/) browser
  extension relays the page's tools to a desktop MCP client (e.g. Claude
  Desktop), so your own agent can author orbs in the open editor tab. Follow the
  MCP-B connection guide.
- **Chrome's built-in agent** — on Chrome 149+, enable
  `chrome://flags/#enable-webmcp-testing`; the browser's agent can discover and
  call the tools directly.

The editor tab must stay open — the tools live in the page, run with no backend,
and nothing you author is uploaded anywhere.

## Tell the agent how the format works

The single most useful first call is **`get_authoring_guide`** — it returns the
GLSL contract (how your fragment body is assembled, the standard uniforms and
varyings you get for free, the body must write `gl_FragColor`), the binding
signals/ops/curves, naming rules, and limits. Point your agent at it and its
shaders will compile far more often on the first try. `get_shader` and
`get_structure` show the current orb.

## The tools

**See the orb**
| Tool | Returns |
| --- | --- |
| `get_orb_state` | Name, palettes, controls + values, voice state, mood |
| `get_shader` | Fragment body, vertex, the injected prelude, declared uniforms, compile log |
| `get_structure` | Metadata, uniforms, fields, bindings (indexed), palettes |
| `get_authoring_guide` | The full authoring contract — call this first |

**Live preview values** — `set_control`, `set_palette`, `save_palette`,
`set_mood`, `preview_voice_state`

**Shaders** — `set_fragment_shader`, `set_vertex_shader`
(compiled before they apply; on a compile error nothing changes and the
compiler log comes back so the agent can fix and retry — the last good orb keeps
rendering)

**Structure** — `add_uniform`, `remove_uniform`, `add_control`,
`remove_control`, `add_binding`, `remove_binding`, `set_meta`

**Import / export** — `export_orb` (get the finished `.orbis`), `load_orb`
(replace the whole orb)

## A typical session

A good authoring loop looks like:

1. `get_authoring_guide` → learn the contract.
2. `add_uniform` for any new value the shader needs (e.g. `uGlow`).
3. `set_fragment_shader` with the GLSL. If it fails, the tool returns the
   compiler log — fix and call again. The preview updates the moment it compiles
   clean.
4. `add_control` to expose a slider or color the user can tweak (this creates a
   uniform, a settings field, palette entries, and the binding in one step).
5. `add_binding` to make it react — e.g. bind `bot.level` into `uGlow` so the
   orb brightens when ORBIS speaks, or `mood.arousal` for energy.
6. `preview_voice_state` / `set_mood` to check how it looks while listening,
   thinking, and speaking.
7. `export_orb` to get the `.orbis`, then **Import** it in ORBIS (custom-orb
   import is part of the paid customization unlock).

Because the agent and the editor share one source of truth, you'll see the orb
morph step by step as each tool lands — which is the whole point.
