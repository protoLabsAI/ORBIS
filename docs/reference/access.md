# Access & privacy

ORBIS is **single-owner** software that runs on **your** hardware. Your memory,
config, and audio stay on your Mac unless you point a model or delegate at a
remote endpoint. This page is the reference for the access controls; for the
posture behind them, see [Privacy](/explanation/privacy).

## One owner

ORBIS is built for a single owner — you. There's no multi-tenant cloud, no
shared profile, no account system. The orb is yours.

## What's local vs. remote

| Stays on your Mac | Leaves only if you configure it |
| --- | --- |
| Your memory (sessions, facts, reminders, inbox) | The **LLM** — if you choose a hosted provider instead of a local one. |
| Your `orbis.yaml` config and persona | **STT / TTS** — if you choose a hosted backend instead of the local default. |
| Mic capture, echo cancellation, the default voice pipeline | **Delegates** — the external agents/endpoints you add. |

The defaults are local: a local LLM (MLX/Ollama), local STT (Whisper/Parakeet),
local TTS (Kokoro). Going remote is opt-in, per choice.

## Owner API key

The **owner API key** (Settings → System → Access) gates privileged access to
ORBIS's API.

- **Single-user, single-machine installs** — leave it empty; there's nothing to
  gate.
- **Multi-machine / tailnet** — set it when ORBIS is reachable beyond
  `localhost` (e.g. over a tailnet), so only the owner can drive it.

## Speaker verification (optional)

By default ORBIS runs in **owner-trust** mode — it assumes the person talking is
you. You can optionally enable a **speaker gate**: a voiceprint check that
verifies it's the owner's voice before acting, and chooses how to handle a
stranger (warn / refuse). Configured under `persona.behavior.speaker_gate`.

## Webhooks

The Stripe webhook endpoint (if you use billing features) is intentionally
unauthenticated — its security is the request signature, not a key.

## See also

- [Privacy](/explanation/privacy)
- [Config reference](./config)
