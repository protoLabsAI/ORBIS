# Engagement modes & activation — design spec

Status: **DESIGN** (2026-06-02). Stage 0 (sidebar mic-mute toggle,
`web/src/plugins/mic-toggle/`) shipped. The interleaved build order now lives in
`surface-plan.md` (plan of record) — modes are interleaved with the widget work on
a shared provider-registry spine, not built as a standalone track. This doc remains
the detailed design for the *modes* half.

## The problem

Today ORBIS has exactly one way to engage: **push-to-talk** — double-click the
orb (or the new sidebar mic toggle) to open the mic, it stays hot until you
toggle it off, and every utterance goes to the one conversational agent, which
answers each turn (and may use tools / delegate). That's a single point in a
much larger space the user wants:

- **Hands-free activation** — an optional wake word to start listening, and an
  automatic "stop listening after I've been quiet a while" timer, so you're not
  reaching for a toggle.
- **Different things to *do* with what it hears** — not always "have a
  conversation." Sometimes: route me straight to a specific delegate. Sometimes:
  just take notes while I rant and hand me a clean version. Sometimes: be a
  rubber duck — listen quietly and reflect back insights when I stop.

The key realization: the user is describing **two orthogonal axes**, not one
list of modes. Keeping them separate is what makes this tractable.

---

## Two axes

### Axis 1 — Activation (how/when the mic is hot)

A small state machine for the microphone. The new sidebar mic toggle is the
**master switch** on top of it.

```
        ┌─────────┐   master mute (sidebar toggle)   ┌─────────┐
        │  MUTED  │ ◄──────────────────────────────► │ (any)   │
        └────┬────┘   nothing flows; wake word OFF    └─────────┘
             │ unmute → enters the configured activation style:
             ▼
   ┌───────────────────── activation style (config) ─────────────────────┐
   │                                                                       │
   │  push-to-talk        wake-word              open-mic                  │
   │  ───────────         ─────────              ────────                  │
   │  manual open each    ARMED ──phrase──►      always HOT                │
   │  turn (orb dbl-      LISTENING              (never auto-closes)       │
   │  click / hold)       ▲        │                                       │
   │       │              │ wake   │ auto-close                            │
   │       ▼              │ word   ▼ (trailing silence ≥ listen_window_s)  │
   │   LISTENING ─────────┴────────► back to ARMED / MUTED                 │
   └───────────────────────────────────────────────────────────────────────┘
```

States:
- **MUTED** — mic gated at the Rust layer (`engine.is_listening() == false`,
  frames dropped in `socket.rs`). Wake word is also off — truly silent. This is
  the default and what the sidebar slashed-mic shows.
- **ARMED** *(wake-word style only)* — a cheap always-on wake-word detector
  runs; the heavy STT pipeline stays gated until the phrase fires. No transcripts,
  no LLM, low CPU.
- **LISTENING (hot)** — mic open, STT + pipeline active. Entered by: wake word
  fired, manual unmute, orb double-click, or push-and-hold.
- **auto-close** — after `listen_window_s` of *trailing* silence the listening
  window closes (→ ARMED if wake-word, → MUTED otherwise). This is the user's
  "listen and then stop listening time."

Two distinct silence timers — don't conflate them:
| Timer | Scale | Job | Lever |
|---|---|---|---|
| **end-of-utterance** | ~0.4s | "you finished a sentence → my turn" | `VAD_STOP_SECS` (exists) |
| **listen-window close** | ~8–20s | "you've gone quiet → stop listening" | `listen_window_s` (new) |

Precedence (important): **manual mute is king.** The sidebar toggle and orb
double-click are hard overrides that beat the wake word. Within "not muted" you
pick *one* activation style. Push-to-talk and wake-word respect the auto-close
timer; open-mic never auto-closes.

#### Wake word — implementation note (decision deferred)
The cheap-and-correct path is a dedicated lightweight wake-word model
(**openWakeWord**, ONNX, runs local on Apple Silicon) sitting *before* the main
STT gate, so Whisper/Parakeet doesn't run until the phrase fires. Fallback: keep
the STT always-on and keyword-spot its transcript (simpler, but burns STT
continuously and defeats the "armed is cheap" point). Recommend openWakeWord;
flag as its own spike. Custom phrase ("Hey Orbis" / user-chosen) is a later nicety.

### Axis 2 — Engagement mode (what it does with what it hears)

Once audio is flowing, the **mode** decides routing, response cadence, and how
chatty Orbis is. A mode is just a profile over a few knobs:

| Knob | Values |
|---|---|
| `routing` | `main-agent` · `delegate:<name>` · `passive-capture` |
| `cadence` | `per-turn` (answer each turn) · `on-stop` (only when I'm done) · `silent` |
| `interjection` | `full` (tools+delegation) · `backchannel-only` · `none` |
| `on_stop_action` | `none` · `summarize-note` · `reflect-insights` |
| `persist` | `none` · `note` (saved to memory) |

The four concrete modes the user described, expressed as profiles:

#### 1. Converse — *"Auto"* (default)
The agent we already have. `routing: main-agent`, `cadence: per-turn`,
`interjection: full`. Orbis decides whether to answer directly, use a tool, or
delegate/orchestrate. This is "auto" — full agency. No behavior change from today;
it just becomes the named default mode.

#### 2. Focus → *[delegate]* — *"toggle straight to one of the agents"*
Pick a delegate from the registry (`agent/delegates.py`); every utterance routes
straight to it (A2A / ACP via the same dispatch `orchestrate._run_step` uses).
`routing: delegate:<name>`, `cadence: per-turn`, `interjection: backchannel-only`.
Orbis becomes a thin relay/narrator — it does **not** try to answer itself or
re-plan; it just pipes you to, say, `codeBot` and speaks the delegate's reply.
Like switching the active app. The sidebar shows *which* delegate is in focus
(its robot icon). Say "back to Orbis" / switch the chip to exit.

#### 3. Notes — *"listen to me rant, then make it concise"*
Passive capture. `routing: passive-capture`, `cadence: on-stop`,
`interjection: none`, `on_stop_action: summarize-note`, `persist: note`. Orbis
stays quiet (no per-turn answers, no backchannel, no micro-ack — these are
already off by default in native mode) and accumulates the transcript. It acts
when you **stop** (trailing silence ≥ `wrap_silence_s`, or a wrap phrase like
"make a note" / "that's it"): cleans the rant into a concise, structured note
(TL;DR + bullets), **saves it to memory**, and reads back a one-line confirmation
("Noted — three action items, saved"). Emphasis is the record, not a dialogue.

#### 4. Rubber duck — *"gather insights quietly, deliver when I stop"*
Passive listening, active reflection. `routing: passive-capture`, `cadence:
on-stop`, `interjection: none`, `on_stop_action: reflect-insights`. Like Notes
in plumbing — accumulates silently, fires on-stop — but the on-stop synthesis is
*reflection*, not a record: surface the key tension, ask the question you're
circling, point out a connection or contradiction. It's a thinking mirror; it
doesn't solve unless asked, and by default doesn't persist. Distinct from Notes:
Notes hands you a clean artifact; Rubber-duck talks back with insight.

Notes and Rubber-duck share **all** the passive-capture machinery (accumulate +
on-stop trigger); they differ only in the on-stop prompt and whether they persist.
Build one, get both.

---

## How the axes compose

They're independent. Examples:
- push-to-talk + Converse = **today's behavior**.
- wake-word + Notes = "Hey Orbis" → it captures until you trail off → saves a note.
- open-mic + Rubber-duck = leave it listening through a whole think-aloud session;
  it reflects each time you pause for a while.

So the UI exposes both: the **mic toggle** (master mute, Axis 1) we just shipped,
an **activation-style** setting (push-to-talk / wake-word / open-mic, in Settings),
and a **mode switcher** in the chrome (Axis 2).

---

## Where it hooks in (grounded in the current pipeline)

Pipeline order today (`app.py:run_bot`, ~1570–1666):
`transport.input → EchoGuard → SpeakerGate → RTVI → STT → AudioTagsTap →
AskGate → user_agg → BargeInGate → MicroAck → backchannel → delivery →
LLM → StallWatchdog → TTS → transport.output → …`

- **Activation / mic gating** is already real: `set_mic_listening` Tauri command
  → `engine.listening` AtomicBool → frame drop in `socket.rs:153`. The
  auto-close timer and ARMED state extend *this* layer (Rust-side, for latency).
  Wake-word detector slots in front of the STT gate.
- **Mode routing** is Python-side. Add an `EngagementModeProcessor` right after
  `AudioTagsTap` (before `AskGate`), mirroring **`voice/ask_gate.py`** — the
  existing pattern for intercepting a `TranscriptionFrame` and either swallowing
  it or passing it through:
  - *Converse*: pass-through (no change).
  - *Focus delegate*: dispatch the transcript straight to the delegate (reuse the
    sticky `A2AClient` / `dispatch()` path from `agent/orchestrate.py:_run_step`),
    stream the reply via `DeliveryController.deliver(...)`, swallow from the LLM path.
  - *Notes / Rubber-duck*: append `frame.text` to a per-session buffer, suppress
    the normal LLM turn; on the **wrap trigger** (a `wrap_silence_s` timer armed on
    `UserStoppedSpeakingFrame`, or a wrap phrase) run synthesis (micro-LLM
    summarize / reflect prompt) → `deliver(..., NEXT_SILENCE)`; Notes also persists
    to the memory subsystem.
- **State to the frontend**: add `engagementMode` (+ `focusDelegate?`) to
  `VoiceSnapshot` (`web/src/voice/state.ts`); publish an `"engagement-mode"` event
  over the existing SSE bus (`voice/sse_bus.py`), handled in `useVoiceBridge.ts`.
- **Switching mode**: an `/api/engagement/mode` endpoint (mode logic is Python),
  not a Tauri command — only the mic gate needs to be Rust-fast. The frontend mode
  switcher POSTs there; the SSE event echoes the new mode back so all surfaces agree.
- **Config**: a new `engagement:` block in persona YAML — default mode,
  activation style, `listen_window_s`, `wrap_silence_s`, wrap phrases, and which
  delegates are focus-able. Runtime-tunable via the `.env` override file like the
  other voice knobs.

The Explore map confirms: **none of this needs new infrastructure** — it's
assembling AskGate (intercept), `deliver()` (speak), `orchestrate`/micro-LLM
(synthesize), the SSE bus (state), and the existing mic gate (activation).

---

## Frontend surface

- **Mic toggle** (shipped) — master mute, top-right rail, slashed mic = muted.
- **Mode switcher** — a small control in the same rail (icon per mode: Converse =
  chat bubble, Focus = the delegate's robot, Notes = pencil, Rubber-duck =
  lightbulb/duck). Click to cycle or open a popover list. Shows the focus
  delegate's name when in Focus mode.
- **Orb state reflects mode** — in passive modes the orb shows a calm "capturing"
  state instead of the per-turn "thinking" animation, so it's visually obvious
  Orbis is listening-not-answering. A subtle pulse on the wrap-trigger so you know
  it's about to speak.
- **Activation style** — lives in Settings (push-to-talk / wake-word / open-mic +
  the two timing sliders), not the rail; it's set-once, not per-moment.

---

## Staged build plan

Ship value early; defer the wake-word engine (the one piece with real unknowns).

**Stage 0 — done:** sidebar mic-mute toggle (master switch, Axis 1 floor).

**Stage 1 — mode framework + Notes/Rubber-duck (highest value, no new deps):**
- `engagement:` config block + `EngagementModeProcessor` (pass-through for
  Converse) + `engagementMode` in voiceStore + SSE event + `/api/engagement/mode`.
- Passive-capture buffer + wrap trigger (`wrap_silence_s` timer + wrap phrases).
- Notes (summarize + persist) and Rubber-duck (reflect) on-stop synthesis.
- Mode switcher UI in the rail; orb "capturing" state.

**Stage 2 — Focus → delegate:**
- Route transcripts straight to a chosen delegate; relay replies; focus chip +
  "back to Orbis" exit. Reuses orchestrate's dispatch.

**Stage 3 — auto-close timer (Axis 1):**
- `listen_window_s` trailing-silence auto-mute in the Rust audio layer; surfaces
  the listening-window countdown to the orb.

**Stage 4 — wake word (spike first):**
- openWakeWord spike → ARMED state before the STT gate → "Hey Orbis" arms a
  listening window. Activation-style setting in Settings. Custom phrase later.

---

## Open questions for Josh

1. **Default mode** — Converse, agreed? Or should a fresh launch come up muted +
   Converse (you opt in to listening), which is the current default?
2. **Notes persistence** — save into the existing memory subsystem (recallable
   later) or a separate "notes" store / file you can export? "Make it concise"
   implies you want the artifact somewhere durable — where?
3. **Wrap trigger feel** — for Notes/Rubber-duck, do you want it to fire purely on
   silence (`wrap_silence_s`, ~6–10s), only on an explicit phrase ("okay, what do
   you think?"), or both? Silence-only risks firing mid-thought; phrase-only means
   it never volunteers.
4. **Focus mode scope** — should *every* utterance go to the delegate, or should
   Orbis still catch meta-commands ("switch back", "never mind") so you're not
   trapped? (I lean: Orbis always catches a tiny set of control phrases.)
5. **Wake word** — is "Hey Orbis" the phrase, or do you want it user-settable from
   day one? And is wake-word even wanted before open-mic + auto-close, or is
   "leave it listening, auto-close when I'm quiet" enough for now?
