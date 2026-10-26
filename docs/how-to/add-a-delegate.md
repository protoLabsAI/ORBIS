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
- **ACP coding agent** — a terminal coding agent (protoCLI, OpenCode, Claude
  Code, Codex) ORBIS launches and drives by voice. It has its own guide:
  [Voice-drive a coding agent](/how-to/voice-drive-a-coding-agent).

For protoAgent, copy its access token if authentication is enabled. Paste the
actual token into ORBIS’s **Access token** password field. ORBIS saves it privately
on this Mac and applies it immediately; no environment file or restart is needed.

### The default protoAgent hub

Fresh installs include a local A2A delegate named **hub** at
`http://127.0.0.1:7870/a2a`. ORBIS does not install or supervise protoAgent;
run it as its own persistent service (for example, with protoAgent's launchd
setup) and keep its A2A listener on port 7870. Check it independently with:

```bash
curl -fsS http://127.0.0.1:7870/.well-known/agent-card.json
```

If ORBIS reports that protoAgent is unavailable after boot, start or restart
that service and use **Settings → Brain → Delegates → hub → Test**. Removing or
customizing the hub is safe; ORBIS only migrates an untouched legacy default
from port 7871 once and never rewrites a customized entry.

## Steps

1. Open **Settings → Brain → Delegates** and click **Add delegate**.
2. Pick the type — **A2A agent** or **OpenAI-compat**.
3. Fill in the shared fields:
   - **Name** — lowercase, no spaces (e.g. `helm`). This is what the model uses
     in `delegate_to(target=…)`.
   - **Description** — *be specific*. The model reads this to decide *when* to
     pick this delegate over another. "Chief of staff — sitreps, planning, fleet
     delegation" beats "an agent".
   - **URL** — for A2A, the JSON-RPC endpoint (often ending in `/a2a`); for
     OpenAI-compat, the base URL ending in `/v1`.
4. Fill in the type-specific fields:
   - **A2A** — paste the **Access token**. ORBIS selects bearer authentication
     automatically when you supply a token. Leave authentication empty for an
     open agent; select `apiKey` only for a legacy endpoint that requires it.
   - **OpenAI-compat** — the **Model** id and, if the endpoint needs auth, the
     **API key env var**. Optionally a **system prompt**.
5. Click **Connect** (or **Save and connect** when editing). ORBIS verifies
   authentication with a read-only A2A request before saving. A public agent
   card alone does not count as a successful connection.
6. If a check fails, follow the error shown below the form. Use **Test** to
   check again without saving. Saved tokens are hidden; editing the URL
   requires entering the token again to avoid sending it to a different agent.

## Use it

Just talk: ask the orb something that fits the delegate's description — e.g.
*"ask helm for the project status."* The orb acknowledges, hands off, speaks the
result. For multi-step asks, it can [orchestrate](/explanation/the-agent-model#sync-vs-background)
across delegates in the background.

## Troubleshooting

- **Authentication rejected** — paste the current protoAgent token into
  **Access token**. Do not paste it into the advanced environment-variable field.
- **Cannot reach the agent** — open protoAgent and check its endpoint URL
  (usually `http://127.0.0.1:7870/a2a`).
- **Advanced environment variables** — existing `credentialsEnv` setups remain
  supported. This field takes a name such as `PROTOAGENT_AUTH_TOKEN`, not a key.
  Desktop runtime variables live in
  `~/Library/Application Support/studio.protolabs.orbis/.env` and require a
  restart. Pasting an Access token replaces that reference without a restart.
- **Marked "⚠ unconfigured"** in the list — the runtime rejected the entry; fix
  the config or it won't be offered to the model.
- **The orb never picks it** — sharpen the **description**; that's the only
  thing the model uses to choose.

## See also

- [Agent reference → Delegates](/reference/agent#delegates)
- [The agent model](/explanation/the-agent-model)
