# Add a delegate

A **delegate** is a sub-agent ORBIS can hand work to. This guide adds one and
confirms the orb can reach it. For the concepts, see
[The agent model](/explanation/the-agent-model); for every field, the
[Agent reference](/reference/agent#delegates).

## Before you start

Decide which kind you're adding:

- **A2A agent** — another agent that speaks the Agent2Agent JSON-RPC protocol
  (e.g. a peer in your fleet). You'll need its endpoint URL.
- **OpenAI-compat** — any `/v1/chat/completions` endpoint you want to treat as a
  specialist (a bigger model, a service).

If the endpoint needs a secret, put it in your **`.env`** on the host first —
the UI references the env-var *name*, never the value.

## Steps

1. Open **Settings → Agent → Delegates** and click **Add delegate**.
2. Pick the type — **A2A agent** or **OpenAI-compat**.
3. Fill in the shared fields:
   - **Name** — lowercase, no spaces (e.g. `ava`). This is what the model uses
     in `delegate_to(target=…)`.
   - **Description** — *be specific*. The model reads this to decide *when* to
     pick this delegate over another. "Chief of staff — sitreps, planning, fleet
     delegation" beats "an agent".
   - **URL** — for A2A, the JSON-RPC endpoint (often ending in `/a2a`); for
     OpenAI-compat, the base URL ending in `/v1`.
4. Fill in the type-specific fields:
   - **A2A** — pick an **Auth scheme** (`apiKey`, `bearer`, or none) and, if
     used, the **Credentials env var** holding the secret.
   - **OpenAI-compat** — the **Model** id and, if the endpoint needs auth, the
     **API key env var**. Optionally a **system prompt**.
5. Click **Test** — ORBIS pings the endpoint and reports reachability and
   latency *before* you save, so you can fix a typo without committing it.
6. Click **Create**.

## Use it

Just talk: ask the orb something that fits the delegate's description — *"ask
Ava for the fleet status."* The orb acknowledges, hands off, and speaks the
result. For multi-step asks, it can [orchestrate](/explanation/the-agent-model#sync-vs-background)
across delegates in the background.

## Troubleshooting

- **Test fails** — re-check the URL (A2A endpoints usually end in `/a2a`,
  OpenAI in `/v1`) and that the credentials env var is set in `.env` on the
  host.
- **Marked "⚠ unconfigured"** in the list — the runtime rejected the entry; fix
  the config or it won't be offered to the model.
- **The orb never picks it** — sharpen the **description**; that's the only
  thing the model uses to choose.

## See also

- [Agent reference → Delegates](/reference/agent#delegates)
- [The agent model](/explanation/the-agent-model)
