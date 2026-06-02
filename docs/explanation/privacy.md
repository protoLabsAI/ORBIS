# Privacy

ORBIS's privacy story is simple because its architecture is simple: it's
**single-owner software that runs on your own machine.** This page explains what
that means and where the boundaries are.

For the specific controls, see the [Access & privacy reference](/reference/access).

## On your hardware, for one owner

There's no ORBIS cloud sitting between you and the orb. The app runs on your
Mac; the brain (the sidecar) runs on your Mac, bound to `127.0.0.1`. Your
memory — every session, fact, reminder, and the persona — lives in a local store
on that machine. There's no account, no multi-tenant server, no shared profile.
It's *your* orb, on *your* computer.

## What leaves the machine — only what you choose

ORBIS reaches the network in exactly three places, and each is your choice:

- **The language model**, *if* you pick a hosted provider. The local options
  (MLX, Ollama) keep even this on-device.
- **Speech-to-text / text-to-speech**, *if* you pick a hosted backend. The
  defaults (Whisper/Parakeet, Kokoro) are local.
- **Delegates** — the external agents you explicitly add and point the orb at.

Out of the box, with local models and no delegates, **nothing about your
conversations leaves your Mac.**

## The trust model

Because it's single-owner, ORBIS trusts the person at the keyboard and mic by
default ("owner-trust"). Two optional controls tighten that when you need them:

- The **owner API key** gates the API when ORBIS is reachable beyond localhost
  (e.g. on a tailnet).
- The **speaker gate** verifies it's *your* voice before acting, and decides how
  to treat a stranger.

Both are opt-in — a single-machine install doesn't need either.

## Why this shape

A companion that remembers you is only comfortable if that memory is *yours*.
Keeping everything local, single-owner, and opt-in-to-share is what makes ORBIS
something you can actually confide in — not a profile in someone else's
database.

## See also

- [Access & privacy reference](/reference/access)
- [Memory & persona](/explanation/memory-and-persona)
