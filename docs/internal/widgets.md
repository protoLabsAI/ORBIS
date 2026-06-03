# Rendered widgets — holistic design spec

Status: **DESIGN** (2026-06-02). Build order + decisions now live in
`surface-plan.md` (plan of record). Companion to
`docs/internal/engagement-modes.md` — the two are deliberately linked (Notes mode
feeds the note widget; Focus→delegate pairs with a delegate console).

> **Decisions locked 2026-06-02:** docked-default + pop-out · co-drive is
> Orbis-visible · interleaved build on a provider-registry spine · all widget UI on
> the design system. See `surface-plan.md`.

## The vision (Josh, 2026-06-02)

> Orbis should be all rendered widgets — both in the app and as separate panels,
> since we have the native Tauri capabilities. A note editor / notepad, a weather
> widget, a console emulator to render the ACP delegate's delegation, a chat
> window to monitor the A2A / OpenAI delegations — watch what's happening between
> Orbis and the delegates. And within each, an input so I can interject as an
> operator and get into the flow — pulled in as part of a tool caller / HITL.

The orb stops being *the* interface and becomes the centerpiece of a **desktop
surface**: a set of glanceable and interactive panels, each a live view onto some
slice of ORBIS's world, several of them able to take operator input back into the
agent loop.

## The unifying model: widgets are bidirectional ports, not just displays

The trap would be to build five bespoke panels. Instead, one model with **three
planes**. Every widget is defined by what it does on each plane:

1. **Render plane** — *where* it shows: docked in the main window, or popped out
   to its own native window. Same component, two mounts.
2. **Data plane** — *what it reads*: a live event stream (SSE), a store (SQLite),
   or an external source (a weather API).
3. **Interjection plane** — *what it writes back*: nothing (read-only), a store
   (note edits), or **the running agent / a live delegation** (operator input
   injected into the flow).

A widget is just a choice on each plane. That's what keeps "weather" and "ACP
console" the same kind of object.

### Three widget classes (fall out of the planes)

| Class | Data plane | Interjection plane | Examples | Backend lift |
|---|---|---|---|---|
| **Glance** | external/ambient, read-only | none | weather, clock, calendar, system stats | ~none — proves the substrate |
| **Content** | a store (CRUD) | the store | note editor, reminders, inbox | a table + `/api/` CRUD |
| **Agent** | the SSE event plane | **back into the agent / a delegation** | delegate console (ACP), delegation chat monitor (A2A/OpenAI), orchestration monitor | event plane + text-HITL |

This taxonomy also *is* the build order: Glance proves render+data with zero agent
coupling; Content adds stores; Agent adds the genuinely-missing event + interjection
infrastructure.

---

## Render plane — dock + pop-out from one component

The requirement "both in the app and as separate panels" = **one widget component,
two host mounts**. A widget never knows where it lives.

```
WidgetDef { id, title, icon, render(), defaultSurface: 'dock'|'window', class }
                              │
              ┌───────────────┴────────────────┐
        docked in main window            floated native window
        (resizable dock / grid)          (Tauri WebviewWindow)
        <WidgetDock> renders <render/>   label "widget-<id>", loads ORBIS's own
                                          bundle at ?widget=<id>; React root reads
                                          the param and renders just <render/>
```

What exists vs. what's new:
- **Exists (production):** the multi-window machinery — `open_or_focus_console`
  + `WebviewWindowBuilder` (`src-tauri/src/lib.rs:529`), with label-based reuse
  (focus if already open). Built for fleet agents in ORBIS#325.
- **The gap:** those windows load *external* URLs (the fleet agent's own console).
  ORBIS's own widgets need a window that loads **ORBIS's own** bundle and renders a
  *specific widget* as root. So:
  1. Generalize `open_or_focus_console` → a generic Tauri command
     `open_widget_window(id, title, w, h)` that loads
     `WebviewUrl::App("index.html?widget=<id>")` (internal, not external).
  2. A `WidgetWindowRoot` in React: read `?widget=` (or `window.label`) at boot;
     if present, render that one widget full-bleed instead of the main app. (The
     map confirms there's **no** per-window routing today — this is net-new but small.)
  3. The in-app **dock**: a resizable region/grid in the main window where docked
     widgets live. Each widget's chrome has a **pop-out / dock-in** toggle that
     moves it between surfaces. Plus persist which widgets are open + where, so the
     workspace restores on relaunch.
- **Reuse, don't fork:** the existing plugin/slot registry (`registry.ts`,
  `PluginHost.tsx`, 8 slots, 11 plugins) is the right substrate to *extend* —
  widgets are registered like plugins, but with a `surface` and a render that can
  mount in either a dock slot or a window root. Don't build a parallel system.

---

## Data plane — what widgets feed on

- **Live stream = the SSE bus** (`voice/sse_bus.py`, `/api/events`). Proven,
  JSON named events: `bot-state`, `transcript`, `tool-call`, `delegation-progress`,
  `llm`, `session`. Add a React **`useSSEBus(eventName)`** hook so any widget
  subscribes declaratively. The bus has no per-topic filtering today (subscribe-to-
  all, queue drops at 64) — add a `topic`/`delegateId` field to events and
  filter client-side to start; revisit server-side filtering only if volume bites.
- **Stores = the SQLite memory DB** (`memory/db.py`). `reminders` and `inbox`
  already have full `/api/` CRUD — those widgets are nearly free. `facts`,
  `sessions`, `personality`, `mood` are logged but unexposed. **No notes table** —
  the note editor needs one (`notes` + `/api/notes` CRUD), or repurpose `facts`
  with `subject="note"` (cleaner to add the table).
- **External = a fetch.** Weather is just an HTTP call from the widget (or a tiny
  `/api/weather` proxy if we want a key server-side). Deliberately trivial — it's
  the canary that proves render+data without touching the agent.

---

## Interjection plane — operator into the flow (the hard, important part)

This is the heart of what Josh wants: *"interject as an operator and get into the
flow — pulled in as part of a tool caller / HITL."* Today HITL is **voice-only**:
`ask_user` → `PendingAsk` → `AskGate` swallows the next spoken transcript
(`agent/user_state.py`, `voice/ask_gate.py`). **There is no typed-text path into
the agent at all** — that's the single biggest missing piece.

The clean model: an **operator message** is a first-class input addressed to a
**target**. Three targets cover everything Josh described:

| Target | Meaning | Mechanism | Status |
|---|---|---|---|
| `pending-ask` | answer the question the agent is currently blocked on | resolve `PendingAsk.future` with typed text (text twin of AskGate) | **new** `POST /api/operator/answer` |
| `delegate:<id>` | speak into a live delegation, co-driving it | ACP: another `session/prompt` on the same session · A2A: another message on the same `contextId`/input-required task | **new** `POST /api/operator/inject` |
| `agent` | start/steer a normal turn by text | inject a user turn (text twin of a voice turn) | **new** `POST /api/operator/turn` |

The key insight that satisfies "pulled in as a tool caller": for a running
delegation, **the operator and Orbis are both clients of the same delegate
session.** When you type into a delegate console mid-flight, your text is just
another `session/prompt` on the *same* ACP session (or another message on the same
A2A `contextId`) — so you and Orbis **co-drive** the delegate. The operator
message isn't a side-channel; it's an additional input the in-flight tool call
consumes, exactly like the agent's own messages. That's the whole "get into the
flow" requirement, and it falls out naturally because ACP sessions and A2A
contexts are already long-lived and multi-message.

To make the **monitor** widgets real (watch Orbis↔delegate traffic), wire the
already-existing-but-**unused** progress callbacks into structured SSE events:
- ACP `AcpClient.prompt(progress_callback=...)` (`acp/client.py:275`) → publish
  `delegate.message` / `delegate.tool` events instead of only narrating to voice.
- A2A `A2AClient.send(progress_callback=...)` (`a2a_outbound.py:155`, currently
  unused in dispatch) → publish `delegate.message` / `delegate.status`
  (working / input-required / completed / failed).
- OpenAI delegates are one-shot (`agent/delegates.py:547`) — show request/response
  pairs only; no stream.

New structured events (one shape, `delegateId` + `sessionId` for filtering):
`delegate.session` (open/close) · `delegate.message {role: orbis|delegate, text}` ·
`delegate.tool {title, status}` · `delegate.status {state}`. A console widget filters
to one `delegateId`; the chat monitor shows all and lets you pick.

---

## The concrete first widget catalog

Each tagged with class + what it needs that doesn't exist yet.

- **Weather** *(Glance)* — external fetch only. **Needs: nothing new.** The canary
  that proves dock + pop-out + `useSSEBus`/data hook end-to-end with zero agent risk.
- **Note editor / Notepad** *(Content)* — CRUD over a new `notes` table; **receives
  Notes-mode output** from the engagement-modes spec; "ask Orbis about this" selects
  text → `/api/operator/turn`. **Needs: `notes` table + `/api/notes` CRUD.**
- **Delegate console (ACP)** *(Agent)* — terminal-style live stream of one ACP
  delegate's tool calls + message chunks; input box → `session/prompt` co-drive.
  Pairs with Focus→delegate mode. **Needs: structured ACP events + `/api/operator/inject`.**
- **Delegation chat monitor (A2A/OpenAI)** *(Agent)* — chat-bubble view of
  Orbis↔delegate messages across delegations, filter by delegate, input box →
  inject into that delegate's task/context. **Needs: structured A2A events + inject.**
- **Orchestration monitor** *(Agent)* — the live `orchestrate()` ReAct loop: goal,
  steps, the current pending ask. The pending-ask answer box is the text-HITL path.
  **Needs: orchestration-state events + `/api/operator/answer`.** (Ties directly to
  the HITL we already shipped for voice.)

---

## What exists vs. what's net-new (honest accounting)

**Exists, reuse:** multi-window create+reuse (`open_or_focus_console`); plugin/slot
registry; SSE bus + `/api/events`; ACP & A2A progress callbacks (present but unused
for UI); reminders & inbox CRUD; memory SQLite; voice HITL pipeline.

**Net-new, in rough effort order:**
1. `useSSEBus(event)` hook + a `topic`/`delegateId` field on events *(small)*.
2. Generic `open_widget_window` + `WidgetWindowRoot` routing by `?widget=` *(small)*.
3. In-app **dock** + pop-out/dock-in toggle + workspace persistence *(medium)*.
4. **Text-HITL**: `/api/operator/{answer,turn,inject}` + a text twin of AskGate *(medium, highest-leverage)*.
5. Structured `delegate.*` SSE events from the wired-in ACP/A2A callbacks *(medium)*.
6. `notes` table + `/api/notes` CRUD *(small)*.

---

## Staged build plan

**Stage 1 — substrate proof (Weather + dock + pop-out).** Build the widget
registry extension, the in-app dock, the generic native-window route, and Weather.
Proves render+data end-to-end with zero agent coupling. Lowest risk, immediately
visible.

**Stage 2 — text-HITL spine (`/api/operator/*` + Orchestration monitor).** The
highest-leverage backend piece: typed operator input into the agent. Surface it via
the orchestration monitor (answer the pending ask by typing). Unblocks every Agent
widget. Pairs with the engagement-modes HITL.

**Stage 3 — Note editor.** `notes` table + CRUD + the widget; wire Notes engagement
mode's on-stop output into it. Ties the two specs together.

**Stage 4 — delegation monitors (ACP console + A2A/OpenAI chat).** Structured
`delegate.*` events + the inject endpoint + the two widgets. The richest, most
"operator co-pilot" piece — built last because it leans on Stages 1, 2, and the
event work.

---

## Open questions for Josh

1. **Dock vs. windows default** — should widgets open **docked in the main window**
   by default (one workspace) and pop out on demand, or open as **floating native
   windows** by default (more "macOS app" feel, more window management)? I lean
   docked-default + pop-out, but you clearly value the native-panel feel.
2. **Co-drive semantics** — when you type into a live delegate console, should
   Orbis **see** your injected message (it's part of the shared session, so it
   would react to it on its next step) or should operator injections be **invisible
   to Orbis** (a private side-channel to the delegate)? Co-drive (Orbis sees it) is
   the simpler, more powerful model, but means you can't quietly correct a delegate
   without Orbis noticing.
3. **Weather — worth it, or a placeholder?** It's the cleanest substrate canary,
   but it's not agent work. Fine as the Stage-1 proof, or do you want the canary to
   be something you'd actually use (a system-stats / calendar glance widget instead)?
4. **Notes store** — same question as engagement-modes Q2: dedicated `notes` table
   (exportable, editable in the widget) vs. fold into `facts`/memory. The note
   *editor* widget pushes me toward a real `notes` table.
5. **Monitor scope** — one chat monitor showing **all** delegations with a filter,
   or **one console window per delegate** (like the fleet console is per-agent)?
   Or both — a monitor that can pop any delegate out into its own console?
6. **Does this re-scope engagement modes?** The Note widget is the natural home for
   Notes mode, and the delegate console is the natural home for Focus mode. Do you
   want to **fold the two specs into one staged plan** (widgets + modes interleaved),
   or keep building modes first (Stage 1 there) and treat widgets as the next epic?
