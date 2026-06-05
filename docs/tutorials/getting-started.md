# Getting started with ORBIS

This tutorial takes you from downloading ORBIS to a working voice companion
that reminds you, hands work to an agent, and responds to an external ping. By
the end you'll have *heard* each of ORBIS's signature behaviours.

> **You'll need:** an Apple-Silicon Mac (M1 or later) on a recent macOS. That's
> it — ORBIS is a regular Mac app you download and open; there's nothing to
> build or clone.

## 1. Download and install

1. Go to **[orbis.protolabs.studio/download](https://orbis.protolabs.studio/download)**
   and download the latest `.dmg`.
2. Open the downloaded file and drag **ORBIS** into your **Applications** folder.
3. Open ORBIS from Applications. It's **signed and notarized by Apple**, so it
   opens cleanly — no right-click-to-open or Gatekeeper workarounds.

On first launch ORBIS asks for **microphone access** — grant it. (If the orb
can't hear you later, toggle ORBIS on under System Settings → Privacy &
Security → Microphone.) A short **first-run setup** walks you through naming it,
picking a voice, choosing your speech (on-device or cloud), and pointing it at a
language model. When you finish, the orb appears — that's your companion, ready.

> ORBIS needs a language model to think. The setup helps you connect one — a
> cloud provider with your own key, or a local model. You can change it any time
> from **Settings → Brain**, or see [Configure the LLM](/how-to/configure-the-llm).

## 2. Have a conversation

Double-click the orb to start listening, and just talk: *"Hey, what can you
do?"* It answers in voice. That's the baseline — speech in, speech out.

## 3. Set a reminder (it speaks on its own)

Say:

> *"Remind me in one minute to stretch."*

It confirms ("I'll remind you in a minute"). Now **keep chatting** about
anything. About a minute later, at a natural pause, the orb brings the reminder
up **on its own** — phrased naturally, not read verbatim. Then ask:

> *"What did you just remind me about?"*

It answers from memory — the proactive line is part of the conversation.

## 4. Hand work to an agent

ORBIS ships with **no delegates** — you add the agents you want it to hand work
to. Configure one first (see [Add a delegate](/how-to/add-a-delegate)), then ask
for it by name:

> *"Ask my agent to do X and let me know."*

It **acknowledges right away** and keeps talking with you. When the agent
finishes, the orb **speaks the result** at the next pause, attributed to the
agent. You didn't wait.

## 5. Ping it from outside (optional — for the terminal-inclined)

Anything on your machine can make the orb speak. ORBIS serves a small local API;
find its port and send a message:

```bash
PORT=$(lsof -nP -iTCP -sTCP:LISTEN | grep -i orbis | grep -oE '127.0.0.1:[0-9]+' | head -1 | cut -d: -f2)
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
