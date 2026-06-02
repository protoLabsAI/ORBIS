# Getting started with ORBIS

This tutorial takes you from a fresh checkout to a working voice companion
that reminds you, hands work to an agent, and responds to an external ping.
By the end you'll have *heard* each of ORBIS's signature behaviours.

> **You'll need:** an Apple-Silicon Mac, the repo checked out, and the
> dependencies from the README's *Running it* section. This is the native
> desktop path; the Docker path is in the README.

## 1. Build and launch

From the repo root:

```bash
./scripts/nuke-and-rebuild.sh --no-voice-processing
```

This does a clean build (~80s) — frontend, Python sidecar, and the Tauri
app. When it finishes, launch the built app **from Finder** (double-click
`ORBIS.app` under `src-tauri/target/release/bundle/macos/`) so macOS
attributes the microphone correctly.

> First launch after a fresh build may ask for microphone permission — grant
> it. If the orb can't hear you, toggle ORBIS off/on in
> System Settings → Privacy → Microphone.

You'll see the orb. It's ready when the sidecar log shows `ORBIS_READY`:

```bash
tail -f ~/Library/Logs/studio.protolabs.orbis/sidecar.log
```

## 2. Have a conversation

Double-click the orb to start listening, and just talk: *"Hey, what can you
do?"* It answers in voice. That's the baseline — speech in, speech out.

## 3. Set a reminder (it speaks on its own)

Say:

> *"Remind me in one minute to stretch."*

It confirms ("I'll remind you in a minute"). Now **keep chatting** about
anything. About a minute later, at a natural pause, the orb brings the
reminder up **on its own** — phrased naturally, not read verbatim. In the log
you'll see `[tool] schedule_reminder` immediately, then
`[scheduler] firing reminder` when it fires.

Then ask:

> *"What did you just remind me about?"*

It answers from memory — the proactive line is part of the conversation.

## 4. Hand work to an agent

ORBIS ships with **no delegates** — you add the agents you want it to hand
work to. Configure one first (see [Add a delegate](/how-to/add-a-delegate)),
then ask for it by name:

> *"Ask my agent to do X and let me know."*

It **acknowledges right away** and keeps talking with you. When the agent
finishes, the orb **speaks the result** at the next pause, attributed to the
agent. You didn't wait.

## 5. Ping it from outside

Anything on your machine can make the orb speak. Find the port and send a
message:

```bash
PORT=$(lsof -nP -iTCP -sTCP:LISTEN | grep python | grep -oE '127.0.0.1:[0-9]+' | head -1 | cut -d: -f2)
curl -X POST http://127.0.0.1:$PORT/api/say \
  -H 'Content-Type: application/json' \
  -d '{"text": "your build just went green", "urgency": "normal"}'
```

The orb voices it at the next pause. Try `"urgency": "urgent"` to interrupt
immediately.

## Where to go next

- **Do more** → the [How-to guides](/how-to/README) — configure the LLM, add a
  delegate, manage reminders, customize your orb.
- **Tune it** → the [Reference](/reference/README) — every config and settings knob.
- **Understand it** → [How ORBIS works](/explanation/how-orbis-works).
