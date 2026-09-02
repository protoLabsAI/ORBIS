# How ORBIS works

ORBIS is a **voice-first AI companion** that runs entirely on your own
Apple-Silicon Mac. This page is the mental model — the moving parts and how
they fit together. It's the *why* and the *shape*; the exact knobs live in
[Reference](/reference/README), and the step-by-step lives in
[How-to](/how-to/README).

## The three pieces

ORBIS is one app made of three cooperating layers:

1. **The orb (the window you see).** A native macOS shell rendering the live
   orb — the visual you talk to. It reflects state: idle, listening, thinking,
   speaking. It's a thin presentation layer; the thinking and the hearing
   happen below it.

2. **The sidecar (the brain).** A local process that runs the voice pipeline
   and the agent: speech-to-text, the language model that routes and responds,
   the tools it can call, the delegates it can hand work to, and your memory.
   It runs on `127.0.0.1` — nothing leaves your machine unless *you* configure
   a remote model or delegate.

3. **The native audio engine.** Captures your microphone and plays the orb's
   voice with low latency, handling echo cancellation so the orb doesn't hear
   itself. Audio is native (not browser) — that's a core reason ORBIS is
   Apple-Silicon-only.

## What happens when you talk

A turn flows top to bottom and back:

```
 you speak ──▶ mic ──▶ speech-to-text ──▶ the model
                                            │
                          ┌─────────────────┤ decides: answer, or use a tool /
                          │                 │ delegate, or schedule a reminder
                          ▼                 ▼
                    tools & delegates   reply text ──▶ text-to-speech ──▶ orb speaks
```

The model is a **router** first: it decides whether to just answer, call a
tool (like setting a reminder), or hand a heavier task to one of your
configured agents (a *delegate*) and narrate the result when it comes back.

## Single-owner, on your hardware

ORBIS is built for **one owner** — you. It runs locally, keeps your memory
locally, and only reaches the network for the models and delegates you point
it at. There's no multi-tenant cloud in the middle. See the privacy model
(coming to this section) for the full picture.

## Always available

ORBIS stays in your **Dock** and also creates a menu-bar orb. Closing the window
hides it; the sidecar and audio engine keep running, so it keeps listening in
the background. Bring it back from either icon. (See the desktop-access
how-to.)

## Where to go next

- **Do something:** [How-to guides](/how-to/README).
- **Look something up:** [Reference](/reference/README).
- **Just try it:** [Getting started](/tutorials/getting-started).
