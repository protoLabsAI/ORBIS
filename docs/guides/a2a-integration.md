# A2A Integration

protoVoice speaks [A2A](https://a2a-protocol.org) (JSON-RPC 2.0 over HTTP) for both inbound and outbound traffic. Other agents in the protoLabs fleet can call us; we can dispatch to them through the `deep_research` and `a2a_dispatch` tools.

## Inbound — another agent calls us

When another agent (e.g. ava) sends us a `message/send` request, we run a one-shot text turn through the active skill's LLM + system prompt and return the result as an artifact.

No voice, no WebRTC. This path is for text-only coordination.

### Register protoVoice in the caller's registry

Assuming the caller uses the same `workspace/agents.yaml` schema (protoWorkstacean / protoAgent fleet):

```yaml
agents:
  - name: protovoice
    url: ${PROTOVOICE_URL:-http://protovoice:7866/a2a}
    auth:
      scheme: apiKey
      credentialsEnv: PROTOVOICE_API_KEY
    skills:
      - name: chat
        description: Conversational assistant with web search and calculator
```

### Auth

Set `A2A_AUTH_TOKEN=<shared secret>` on the protoVoice side. Inbound requests without `X-API-Key: <token>` (or `Authorization: Bearer <token>`) are rejected with `401`.

Omit `A2A_AUTH_TOKEN` for unauthenticated operation (dev / homelab). The agent card's `securitySchemes` adjusts based on whether the token is set.

### Agent card

```bash
curl http://localhost:7867/.well-known/agent-card.json
```

Returns our advertised name, skills, capabilities, auth schemes. Callers typically refresh this on boot.

### Wire format

`POST /a2a`:

```json
{
  "jsonrpc": "2.0",
  "id": "<caller-chosen>",
  "method": "message/send",
  "params": {
    "contextId": "<uuid>",
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "<the user's message>"}]
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": "<caller-chosen>",
  "result": {
    "id": "<artifact-id>",
    "contextId": "<uuid>",
    "status": {"state": "completed"},
    "artifacts": [{
      "artifactId": "<uuid>",
      "parts": [{"kind": "text", "text": "<assistant reply>"}]
    }]
  }
}
```

### Multi-turn

Reuse the same `contextId` across turns. protoVoice keeps a bounded buffer (`A2A_MAX_TURNS`, default 10) per context so the conversation stays coherent without growing unbounded.

### Known limits (inbound)

- **No tool calls in the inbound path.** The text agent is a one-shot chat turn; it can't use `web_search` or `delegate_to`. The voice side uses them freely.
- **No task lifecycle** (`tasks/get`, `tasks/cancel`). Only `message/send`.
- **No inbound streaming.** Our server responds with a single-shot Task; streaming outbound is fully wired, inbound isn't yet.

## Outbound — we call another agent

See [Tools → delegate_to](/reference/tools#delegate_to) and the [Delegates reference](/reference/delegates).

Outbound A2A is one branch of the unified `delegate_to(target, query)` tool. Targets are configured in `config/delegates.yaml`; each entry sets `type: a2a` and provides a URL + auth. The LLM picks the target by name based on the description.

### Streaming (SSE)

Outbound dispatch **prefers `message/stream`** per the [A2A streaming spec](https://a2a-protocol.org/latest/topics/streaming-and-async/). Each `TaskStatusUpdateEvent` with a human-readable message is narrated through the voice pipeline via `delivery.speak_now(source=target)` — users hear "ava: still compiling the sitrep…" in-flight instead of silent waiting. Falls back to `message/send` on SSE errors.

### Reply shape tolerance

Per A2A spec, a completed `Task` may deliver its final assistant text either as an `artifacts[]` entry or via its terminal `status.message.parts[]`. Our client accepts both — if `artifacts` is empty we fall back to the terminal status message. This applies in both the sync (`message/send`) and streaming (`message/stream`) paths.

### Push-notification callbacks

When `A2A_PUSH_URL` + `A2A_PUSH_TOKEN` are set, outbound dispatches attach a `pushNotificationConfig` pointing at our `/a2a/push` endpoint. If the SSE stream drops, or the remote agent completes after we disconnect, they call us back:

- Terminal states (`completed` / `failed` / `cancelled`) → `Priority.TIME_SENSITIVE`
- `input-required` / `auth-required` → `Priority.CRITICAL` (interrupts)
- Mid-task `TaskStatusUpdateEvent` → `Priority.ACTIVE` (wait for the user to ask)

If no voice session is live when a push arrives, the payload is stashed under the active skill slug and replayed at the next connect — see [Delivery Policies → Cross-session replay](/guides/delivery-policies#cross-session-replay-reconnect).

### Auth on `/a2a/push`

If `A2A_PUSH_TOKEN` is set, requests must include the matching token either as `Authorization: Bearer <token>` or as `{"token": "…"}` in the body. Token mismatch → 401. When unset (local dev), no auth is enforced — useful for homelab but don't expose that setup to the open internet.

JWT + JWKS signature verification (per A2A spec's recommended pattern) is a future upgrade; shared-secret mode is the current shipping baseline.

### The legacy `/a2a/callback`

`POST /a2a/callback` predates the spec-aligned `/a2a/push` route and stays for backwards compat with the simpler `{"from":"ava","text":"…"}` payload shape. New integrations should target `/a2a/push`.

## Testing locally

```bash
# agent card
curl http://localhost:7867/.well-known/agent-card.json | jq .

# send a message (no auth)
curl -X POST http://localhost:7867/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0","id":"t1","method":"message/send",
    "params":{"contextId":"ctx-demo","message":{"role":"user","parts":[{"kind":"text","text":"what time is it?"}]}}
  }' | jq .

# with auth
curl -H "X-API-Key: $A2A_AUTH_TOKEN" -X POST http://localhost:7867/a2a ...
```
