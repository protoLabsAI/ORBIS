# ORBIS as a proactive companion

ORBIS isn't only reactive — it can **reach out on its own** (reminders you
set, results that come back later, things you push in from outside), and it
brings them up the way a person would, at a natural pause, in its own voice.
This guide covers what it can do, how to drive it, and the knobs that tune
the behaviour.

Everything here is **on by default** and safe — proactive speech is gated so
the orb never talks over you and never turns into a chatterbox.

---

## What it can do

### Reminders — "remind me…"

Just ask, out loud:

- **One-time:** *"Remind me in ten minutes to take the cookies out."* /
  *"In an hour, tell me to call mom."*
- **Recurring:** *"Every hour remind me to drink water."* /
  *"Every thirty minutes, tell me to look away from the screen."*

The orb works out the timing, confirms briefly, and speaks the reminder at
the right time — at the next natural pause in conversation, not over you. If
your Mac was asleep or the app was closed for a long time, reminders more
than ~24h overdue are dropped silently rather than fired late in a burst.

Under the hood these are two tools — `schedule_reminder` (one-time) and
`schedule_recurring_reminder` — but you never name them; you just talk.

### Hand work off to your agents — "ask Ava…"

ORBIS routes the heavy lifting to the agents you've configured (see
[Configuration](#configuration)):

- **Quick question** you want relayed right away → it asks and reads the
  answer back.
- **Longer job** ("ask Ava to research X and get back to me") → it
  **acknowledges immediately** ("on it — I'll let you know"), works in the
  background, and **speaks the result when it lands**, attributed ("Ava got
  back to me — …"). You keep talking in the meantime. If it's taking a while,
  the orb gives one "still waiting on Ava for that" update so it doesn't feel
  forgotten.

### Get pinged from outside — `POST /api/say`

Anything on your machine can make the orb speak — a script, a cron job, a
webhook:

```bash
curl -X POST http://127.0.0.1:<port>/api/say \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $YOUR_KEY" \
  -d '{"text": "your build just went green", "urgency": "normal"}'
```

- `urgency`: `urgent` (interrupt now) · `normal` (next pause, default) ·
  `low` (when it's relevant).
- `source` (optional): adds attribution ("GitHub says — …"); omit it and the
  orb speaks it in its own voice.

If no voice session is connected, the message is stashed and spoken when you
next connect. Find `<port>` with
`lsof -nP -iTCP -sTCP:LISTEN | grep python`.

### It remembers what it brought up

When the orb proactively says something, that line enters the conversation —
so you can reply "oh thanks" or ask "what was that again?" and it knows, and
it can refer back to it later.

---

## How proactive speech stays polite

You don't configure any of this; it's how delivery works:

- **Natural phrasing.** Reminders/pings/results are re-phrased in character
  ("oh hey, quick reminder — drink some water") rather than read verbatim.
- **Right moment.** Non-urgent items wait for a natural pause; only `urgent`
  interrupts.
- **Never over you.** If you start talking while it's about to speak, it
  holds the line for the next pause.
- **Bid before a pile-up.** If several things queue at once, it asks "I've
  got a couple of updates — want them?" instead of dumping them.
- **No double-speak / no flooding.** A duplicated event can't be said twice,
  and if some producer goes haywire it says so once and then goes quiet.
- **No dead air.** If something gets stuck, it says "give me a second"
  instead of leaving silence.

---

## Configuration

Most behaviour is automatic. The knobs below are optional — set them in the
runtime `.env` (`~/Library/Application Support/studio.protolabs.orbis/.env`,
applied on app restart) or in `config/orbis.yaml`. See
`config/orbis.example.yaml` for the YAML form.

### Models

| Knob | Default | What it does |
| --- | --- | --- |
| `persona.llm.url` / `model` / `api_key_env` | env | The main conversational model. |
| `persona.llm.fallback` / `LLM_FALLBACK_URL` … | off | Backup LLM — if the primary errors (cloud down), the orb fails over for the rest of the session so it keeps talking. |
| `persona.llm.router_model` + `content_model` / `LLM_ROUTER_MODEL`, `LLM_CONTENT_MODEL` | off | Two-model routing — a stronger model for the tool-decision turn, a faster one for narration. |
| `persona.llm.micro_model` / `LLM_MICRO_MODEL` | = main model | Cheap/fast tier for throwaway generation (fillers, acks, natural announcements). Point it at a smaller model to make all the micro stuff cheaper at once. |

### Proactive delivery

| Knob | Default | What it does |
| --- | --- | --- |
| `NATURALIZE_DELIVERIES` | `1` | Phrase proactive deliveries in-character via the micro model. `0` = speak them verbatim. |
| `DELEGATE_ASYNC_TIMEOUT` | `300` | Seconds a background hand-off may run before it reports back with a timeout. |
| `DELEGATE_NUDGE_SECS` | `90` | If a background hand-off runs longer than this, the orb gives one "still waiting on …" status update so a slow agent doesn't feel forgotten (cancelled the moment the result lands). `0` disables. |
| `DELIVERY_DEDUP_SECS` | `12` | Drop an identical delivery repeated within this window. |
| `DELIVERY_STORM_THRESHOLD` / `DELIVERY_STORM_WINDOW_SECS` | `8` / `60` | If more than N deliveries clear the gate within the window, say one "I'm getting a lot of updates" notice then suppress until it subsides. `0` disables. |

### Conversation feel

| Knob | Default | What it does |
| --- | --- | --- |
| `MICRO_ACK_LLM_CHANCE` | `0.25` | Chance a between-turn "thinking" ack is freshly generated by the micro model rather than drawn from the (varied) canned pool. |
| `STALL_SECS` / `STALL_WATCHDOG` | `8` / `1` | After this long with no response, speak a recovery line instead of dead air. `STALL_WATCHDOG=0` disables. |
| `FILTER_INCOMPLETE_TURNS` | `0` | Coalesce fragmented speech ("um, so I…") into one reply instead of answering each fragment. **Off by default** — relies on the model marking turn completeness; can misfire on some models. |
| `INCOMPLETE_LONG_TIMEOUT` / `INCOMPLETE_SHORT_TIMEOUT` | `3` / `2` | When the above is on, how long to wait before re-prompting (bounds a misclassification so it can't hang). |

### External push auth

`/api/say` and `/api/inbox` accept the owner API key (`X-API-Key` or
`Authorization: Bearer`) or a scoped ingest token (`INBOX_INGEST_TOKEN`). In
single-user mode they're open on the local machine.

---

## Notable design decisions

These are recorded in full in `DECISIONS.md`; the user-relevant gist:

- **Router-first.** Heavy reasoning lives in *your* agents (delegates), not
  in-process. ORBIS is the voice frontend that routes to them.
- **Kokoro (PyTorch/MPS) stays the TTS**, not the ONNX path — it's measurably
  faster on Apple Silicon (amendment 2026-05-30).
- **Proactive speech is gated, never barged.** The delivery layer decides
  *when* to speak; the LLM only decides *what*.

---

See also: [`docs/agent-inbox.md`](./agent-inbox.md) (the pull-based inbox),
[`docs/proactive-agent-direction.md`](./proactive-agent-direction.md) (the
architecture + roadmap), and `config/orbis.example.yaml` (full config
reference).
