# ORBIS rendered surface — unified, extensibility-first plan

Status: **PLAN OF RECORD** (2026-06-02). Supersedes the staging in
`engagement-modes.md` and `widgets.md` by interleaving them onto one spine. Those
two docs remain the detailed design for their halves; this is the build order and
the architecture that makes both extensible. Read those for the *what*; read this
for the *how* and *in what order*.

## What this unifies

Three threads converge into one surface:
1. **Engagement modes** (`engagement-modes.md`) — how you engage: activation
   (mute / wake-word / auto-close) × mode (Converse / Focus→delegate / Notes /
   Rubber-duck).
2. **Rendered widgets** (`widgets.md`) — Orbis as a desktop surface of panels,
   docked in-app and popped out to native windows, several able to take operator
   input back into the agent loop.
3. **Extensibility** (this doc's keystone) — adding or removing a tool or a
   delegate type must be *one registration*, not a scavenger hunt across dispatch,
   probe, config, settings UI, monitor, and inject paths.

The Note widget is the home for Notes mode; the delegate console is the home for
Focus mode. They're one system, so we build them interleaved.

## Locked decisions (2026-06-02)

- **Dock default + pop-out.** Widgets open docked in the main window (one
  workspace); each can pop out to its own native window and dock back. Not
  floating-by-default.
- **Co-drive is Orbis-visible.** When the operator types into a live delegation,
  the message lands on the *same* session/context Orbis is driving **and** is
  echoed into Orbis's own conversation context (tagged operator-origin) so Orbis
  reacts to it on its next step. Operator + Orbis co-drive; nothing is hidden from
  Orbis.
- **Interleaved plan, built for extensibility** as tools/delegate types come and
  go. → the provider/capability registry below is Stage 1.
- **Design system, always.** Every widget — docked or popped-out — rides the
  existing token layer (`fg-*` / `brand` / `surface` / `edge`, the orb-linked
  accent) and the shared primitives (`SectionLabel`, `Hint`, `Field`,
  `Button`/`Badge`). No raw `zinc-*`/`amber-*`, no `text-[Npx]` literals — the
  sprawl guard (`tests/test_frontend_native_scope.py`) covers the new code too.
  Pop-out windows load ORBIS's own bundle, so they inherit `index.css` tokens for
  free — another reason the native-panel windows are *internal*, not external URLs.

---

## The extensibility keystone — provider/capability registry

Today, adding a delegate type touches **six** places, all switched on
`delegate.type`:

| Concern | Where it's hardcoded today |
|---|---|
| parse config | `agent/delegates.py:122` `_parse_entry` (a2a/openai/acp branches) |
| dispatch | `agent/delegates.py:386` `dispatch` → `_dispatch_a2a/_openai/_acp` |
| probe | `agent/delegates.py:611` `probe` (per-type branches) |
| health-cache invalidation | `agent/delegates.py:200` `_config_changed` (per-type) |
| config validation | `agent/delegate_config_store.py` `validate_entry` + `_ACP_KEYS` |
| settings UI tile | `web/src/plugins/settings-panel/DelegatesSettings.tsx` (a2a/openai/acp tiles hardcoded) |

That's the same per-role sprawl the design-system plan attacked for CSS — and
widgets would *add* three more per-type concerns (monitor renderer, structured
events, operator inject). Six becomes nine. The fix is to collapse them behind one
interface, source-level (the no-workaround way), before we build the widgets that
would otherwise multiply the sprawl.

### `DelegateAdapter` (Python, new `agent/delegate_adapters.py`)

One object per delegate type, owning everything type-specific. The existing
`_parse_entry`/`dispatch`/`probe`/`_config_changed` bodies move in nearly verbatim
— this is a **refactor, not a rewrite** — plus three new capability hooks for the
widget layer:

```python
@dataclass
class FieldSpec:                       # drives parsing AND the generic settings form
    key: str; label: str; kind: str    # text | secret-env | args | path | number
    required: bool = False; placeholder: str = ""; help: str = ""

@dataclass
class Capabilities:
    stream: bool      # emits intermediate events worth rendering
    inject: bool      # accepts operator co-drive mid-session
    session: bool     # long-lived (sticky context / session) vs one-shot
    oneshot: bool     # request/response only

class DelegateAdapter(Protocol):
    type: str
    capabilities: Capabilities
    def config_schema(self) -> list[FieldSpec]: ...          # ← settings form + validation
    def parse(self, raw: dict) -> "Delegate | None": ...     # ← was _parse_entry branch
    def config_changed(self, a: "Delegate", b: "Delegate") -> bool: ...
    async def dispatch(self, d, query, *, timeout, progress) -> str: ...   # ← _dispatch_*
    async def probe(self, d, *, timeout) -> dict: ...        # ← probe branch
    async def stream(self, session, emit) -> None: ...       # NEW — publish delegate.* events
    async def inject(self, session, operator_text: str) -> None: ...  # NEW — co-drive; raise if oneshot

ADAPTERS: dict[str, DelegateAdapter] = {}
def register_adapter(a: DelegateAdapter) -> None: ADAPTERS[a.type] = a
```

The three current types register their capabilities:

| type | stream | inject | session | oneshot | inject mechanism |
|---|---|---|---|---|---|
| `a2a` | ✓ (card-advertised) | ✓ | ✓ | — | message on the same `contextId` |
| `acp` | ✓ (tool titles) | ✓ | ✓ | — | another `session/prompt` on the same session |
| `openai` | — | — | — | ✓ | n/a (request/response) |

`dispatch()`, `probe()`, `_parse_entry()` become three-line dispatchers to
`ADAPTERS[type]`. **Adding a delegate type = write one adapter + `register_adapter`.**
Everything generic lights up: parsing, validation, the settings form (from
`config_schema`), the health probe, the monitor widget, focus-ability, the inject
endpoint, and the event stream. Later we can even discover adapters via entry
points so a delegate type ships as a plugin — but in-tree registration first.

### Frontend mirror — widget + capability registry

```ts
interface WidgetDef {
  id: string; title: string; icon: ComponentType;
  klass: 'glance' | 'content' | 'agent';
  defaultSurface: 'dock' | 'window';      // locked: 'dock'
  render: ComponentType<WidgetProps>;
}
registerWidget(def)   // extends the existing plugin registry, doesn't fork it
```

A **generic delegate-monitor** widget reads a delegate's `capabilities` (served
from `GET /api/delegate-types`) and adapts: shows a stream pane iff `stream`, an
operator inject box iff `inject`, request/response pairs iff `oneshot`. A type
*may* register a custom renderer keyed by type (ACP terminal vs A2A chat bubbles),
falling back to the generic. So a new delegate type gets a working monitor with
**zero** new widget code; a richer one is opt-in.

### Tools, too

Smaller parallel for the agent's own tool registry (`agent/tools.py`): a tool may
declare optional `ui` metadata (a worldstate-delta emitter, a monitor hint),
replacing the hardcoded `_worldstate_delta_for` switch in `app.py`. Generic
consumers (orchestration monitor, the orb's worldstate) read the metadata. Adding
a tool with a UI footprint stops meaning "also edit app.py's delta switch."

---

## The layered model

**Spine (build once, both specs consume):**
- **P — Provider/capability registry** (above): delegate adapters + tool UI specs.
- **R — Render runtime**: widget registry, in-app dock, generic
  `open_widget_window` + `WidgetWindowRoot` (route by `?widget=`), pop-out/dock-in,
  workspace persistence.
- **D — Data plane**: `useSSEBus(event)` hook; structured `delegate.*` events
  emitted by adapters; a `topic`/`delegateId` field for client-side filtering.
- **I — Interjection plane**: `/api/operator/{answer,turn,inject}` + a text twin of
  `AskGate` (co-drive, Orbis-visible).
- **M — Mode framework**: modes as data profiles; the mode processor (post
  `AudioTagsTap`, mirroring `voice/ask_gate.py`); `/api/engagement/mode`;
  `engagementMode` on `voiceStore`.

**Consumer surfaces (small once the spine exists):** Weather (glance) · Note
editor (content) · Orchestration monitor / Delegate console / Delegation chat
(agent) · Notes & Rubber-duck modes · Focus→delegate mode · activation polish.

---

## Interleaved staged plan

Each stage advances the spine **and** lands a visible surface, and is a focused
PR (or a small set). Order front-loads the extensibility keystone per the directive.

**Stage 0 — done.** Sidebar mic-mute toggle.

**Stage 1 — P: provider registry + generic settings.** Refactor the three delegate
types behind `DelegateAdapter`; expose `GET /api/delegate-types`; make
`DelegatesSettings.tsx` render the New/Edit form from `config_schema` (kills the
hardcoded per-type tiles). *Visible win:* settings get cleaner and a 4th type would
"just work." Pure backend + settings; decision-independent; no dock risk. Keystone
first.

**Stage 2 — R: widget runtime + Weather.** Widget registry, in-app dock,
internal-bundle `open_widget_window` + `WidgetWindowRoot`, pop-out/dock-in,
workspace persistence, `useSSEBus`. Ship **Weather** (glance) as the canary —
render + data + dock + pop-out end-to-end, zero agent coupling. All on the design
tokens.

**Stage 3 — M: mode framework + Notes/Rubber-duck.** Mode profiles + processor +
switcher + `/api/engagement/mode`. Ship the two passive modes (capture / reflect)
— no new deps; high engagement value. Mode switcher is a token-styled rail control.

**Stage 4 — I: text-HITL + Orchestration monitor + Note editor.** The
`/api/operator/*` spine (co-drive, Orbis-visible) + text `AskGate` twin. Surface it
two ways: the **Orchestration monitor** (type to answer the pending ask) and the
**Note editor** (content widget) wired to Notes-mode output (+ `notes` table).
Ties modes ↔ widgets; unblocks every Agent widget.

**Stage 5 — D+P payoff: delegation monitors + Focus mode.** Adapters emit
structured `delegate.*` events; build the **generic delegate monitor** (+ ACP
terminal / A2A chat custom renderers) with the operator inject box, and
**Focus→delegate** mode. All delegate-type-agnostic *because* of Stage 1 — this is
where the keystone pays off. The full operator co-pilot.

**Stage 6 — activation polish.** `listen_window_s` auto-close timer (Rust audio
layer); then the wake-word spike (openWakeWord) → ARMED state. Independent of the
widget work; can slot whenever.

### Backlog (captured, not yet scheduled)

- **Widgets auto-pop on app-hide** (Josh, 2026-06-02) — when the main window is
  hidden (it hides, not quits), docked widgets should pop out to their native
  windows so they stay visible as ambient desktop panels, and re-dock when the
  app is shown. Needs reliable Rust hide/show signals + a reconcile pass + race
  handling against the `beforeunload` re-dock. Deferred from Stage 2b on purpose.
- **Command bar** (Josh, 2026-06-02) — a Raycast / Script Kit-style global-hotkey
  palette to fire ORBIS skills, delegate to agents, run tools, open widgets. The
  keyboard complement to voice (still voice-first; this is the fast manual path).
  Natural fit for the provider/tool registries — it surfaces the SAME actions the
  agent has (tools, delegates via the adapter registry, skills). Likely its own
  epic: global shortcut (tauri-plugin-global-shortcut) → a transparent borderless
  palette window (reuses the 2b window machinery) → an action registry unifying
  tools + delegates + skills + widgets.

---

## Alignment with existing direction locks

- **Native-Mac direction** (`native-audio-direction.md`, Apple-Silicon-only):
  multi-window native panels *deepen* the native investment — pop-out windows are a
  native-only capability. No browser/PWA/WebRTC reintroduced. ✓
- **Agent-first** (`feedback_agent_first`): the Agent-class widgets (monitor /
  co-drive) and Notes/Rubber-duck (capture / reflect) are squarely agent-value, not
  emotional-companion. Mood/emotional layer stays paused; no mood-driven widgets. ✓
- **No workarounds**: the provider registry fixes per-type sprawl at the source
  instead of bolting type special-cases onto each new widget. ✓
- **Focused PRs**: each stage is its own PR (or a tight set). ✓

---

## Remaining open questions (smaller; don't block Stage 1–2)

Carried from the two specs, needed by the stage that consumes them:
- **Notes store** (Stage 3/4) — dedicated `notes` table (exportable, editable) vs.
  fold into `facts`. The note *editor* pushes toward a real table. *(lean: table.)*
- **Wrap trigger feel** (Stage 3) — Notes/Rubber-duck fire on silence
  (`wrap_silence_s`), on an explicit phrase ("what do you think?"), or both.
  *(lean: both.)*
- **Monitor scope** (Stage 5) — one chat monitor with a delegate filter, or one
  console per delegate, or a monitor that pops any delegate into its own console.
  *(lean: the third — monitor that pops out per-delegate.)*
- **Weather vs. other canary** (Stage 2) — keep Weather as the glance canary, or
  use something you'd actually keep open (system stats / calendar)?
- **Wake phrase** (Stage 6) — "Hey Orbis" fixed, or user-settable from day one.
