# HTTP API

Served by the `protovoice` FastAPI app on `PORT` (default 7866).

## Authentication

Every `/api/*` route requires an API key identifying the caller. Send one of:

```http
X-API-Key: <key>
Authorization: Bearer <key>
```

Keys are resolved to users via the roster loaded from Infisical (when `INFISICAL_CLIENT_ID` + `INFISICAL_CLIENT_SECRET` + `INFISICAL_PROJECT_ID` are set) or `config/users.yaml` as a fallback. See [Personas & Skills → Users](../guides/personas-and-skills).

When no user source is configured (empty registry), protoVoice operates in **single-user fallback mode** — all requests resolve to a synthetic `default` user regardless of credentials. Keeps local dev and existing tailnet-only deployments working unchanged. The moment the registry has ≥1 user, auth enforcement kicks in.

`GET /healthz`, `GET /`, and the PWA static assets stay public (no auth). `GET /.well-known/agent-card.json` + `POST /a2a` use their own `A2A_AUTH_TOKEN` shared-secret scheme — see [A2A Integration](/guides/a2a-integration).

## `GET /api/whoami`

Returns the caller's resolved identity + role + access-list + any admin-set viz override. Clients use this to confirm their API key is valid, display the user's name, and branch UI for admin-only controls.

```json
{
  "id": "alice",
  "display_name": "Alice",
  "role": "user",
  "allowed_skills": ["josh", "chef"],
  "pinned_viz": { "palette": "Noir" },
  "auth_source": "infisical"
}
```

- `role` — `"user"` (default) or `"admin"`. Admins see the full drawer (Voice + Orb tabs, admin CRUD when shipped); users see Voice only.
- `allowed_skills` — list of skill slugs the user can activate, or `null` for unconstrained. A one-element list causes the client to render a read-only chip in place of the dropdown.
- `pinned_viz` — optional `{variant?, palette?, params?}` block that overrides the active skill's `viz`. `null` when unset.
- `auth_source` — `"infisical"`, `"file"`, or `"empty"` (single-user fallback; the DEFAULT user resolves as admin).

## `POST /api/users/reload`

Re-fetches the user roster from the active source (Infisical or `config/users.yaml`). Active clients keep their authenticated state until they reconnect; new connections use the refreshed registry. Returns `{"ok": true, "users": [...], "source": "..."}`.

## `GET /`

Returns the browser client HTML. Single-page; click Start to connect.

## `GET /healthz`

Public — no auth. Reports process-wide shape, not per-user state.

```json
{
  "status": "ok",
  "stt_backend": "local",
  "tts_backend": "fish",
  "auth_source": "infisical",
  "user_count": 3,
  "active_sessions": 1,
  "delegates": [{"name": "ava", "type": "openai"}],
  "skills": ["default", "chef", "voice-01"],
  "audio": {
    "half_duplex": false,
    "echo_guard_ms": 300,
    "noise_filter": "off",
    "smart_turn": "off"
  }
}
```

- `auth_source` — `"infisical"`, `"file"`, or `"empty"` (single-user fallback)
- `user_count` — size of the roster (0 in single-user mode)
- `active_sessions` — users with a live voice connection right now

Used by Docker HEALTHCHECK and external monitoring.

## `GET /api/verbosity`

```json
{"verbosity":"brief"}
```

Scoped to the calling user. Each user has their own verbosity setting.

## `POST /api/verbosity`

```http
POST /api/verbosity
Content-Type: application/json

{"level":"narrated"}
```

Accepts `silent` / `brief` / `narrated` / `chatty`. Returns the new value or `{"error":"..."}` on invalid input.

Session-level (module singleton for now); shared across all connected clients. Per-session keying is planned for multi-tenant.

## `GET /api/skills`

```json
{
  "active": "chef",
  "locked": false,
  "skills": [
    {"slug": "default", "name": "Default", "description": "", "viz": {}},
    {
      "slug": "chef",
      "name": "Chef Bruno",
      "description": "An Italian-American chef...",
      "viz": {
        "variant": "fractal",
        "palette": "Ember",
        "params": {"density": 2.4, "speed": 0.45}
      }
    }
  ]
}
```

- `active` — the resolved skill for the calling user. For users with `allowed_skills` set, always falls inside that list (snaps to the first entry when mutable state drifts outside).
- `locked` — `true` when the caller's `allowed_skills` has exactly one entry. Clients use this to render a read-only chip in place of the skill dropdown.
- `skills[]` — filtered to the caller's `allowed_skills` for non-admins. Admins (and unconstrained users) see the full catalog.
- `skills[].viz` — optional orb viz block. The client applies it on skill switch (priority: `whoami.pinned_viz → skill.viz → nothing`). See [Orb visualizer](./orb-visualizer).

## `POST /api/skills`

```http
POST /api/skills
Content-Type: application/json

{"slug": "chef"}
```

Applies on the next Start click — skill is snapshotted at connect time. Returns `{"error":"..."}` if the slug is unknown.

Returns HTTP `403` with detail `"skill '<slug>' not in allowed_skills for user '<id>'"` when a non-admin caller posts a slug outside their `allowed_skills`. Admins can change any user's skill via [`POST /api/admin/skills`](#post-apiadminskills) below.

## `POST /api/admin/skills`

Admin-only. Sets any user's mutable active skill, bypassing the skill selector UI.

```http
POST /api/admin/skills
Content-Type: application/json

{"user_id": "alice", "slug": "chef"}
```

```json
{"ok": true, "user_id": "alice", "active": "chef"}
```

- Requires the caller's role to be `admin` (returns `403` otherwise).
- Updates the target's in-memory `UserState.skill_slug`. Applies on their next connect; active sessions keep their snapshotted skill.
- Does **not** modify `allowed_skills` — for persistent access-list changes, edit `config/users.yaml` (or the `USERS_YAML` Infisical secret) and `POST /api/users/reload`.
- Returns `{"error": "..."}` for unknown `user_id` or unknown `slug`.

## `POST /api/skills/reload`

```http
POST /api/skills/reload
```

Re-reads every `config/skills/*.yaml` + `SOUL.md` from disk. Active sessions keep their captured skill snapshot until they reconnect. Returns `{"ok": true, "skills": [...], "active": "<slug>"}`.

## `POST /api/delegates/reload`

```http
POST /api/delegates/reload
```

Re-reads `config/delegates.yaml` from disk. Safe mid-session — delegate lookup happens per `delegate_to()` call, so in-flight sessions see the new registry on their next dispatch. Returns `{"ok": true, "delegates": [...]}`.

## `GET /api/voice/references`

```json
{"backend": "fish", "references": ["josh_sample_1", "voice_01", "voice_02"]}
```

Returns the Fish server's saved reference IDs. Empty list if `TTS_BACKEND` isn't `fish`.

## `POST /api/voice/clone`

Multipart upload — clones a new voice AND creates a skill pointing at it.

```http
POST /api/voice/clone
Content-Type: multipart/form-data

audio:       <file>       required — 10-30 s WAV/MP3/FLAC/OGG
slug:        <string>     required — lowercase [a-z0-9\-_] 2-64 chars
name:        <string>     optional — defaults to title-cased slug
transcript:  <string>     optional — omit to auto-transcribe via Whisper
description: <string>     optional
```

Response:

```json
{
  "ok": true,
  "slug": "alex",
  "name": "Alex",
  "transcript": "...",
  "auto_transcribed": true
}
```

Side effects:

- Saved reference on the Fish server (`POST /v1/references/add`)
- New YAML at `config/skills/<slug>.yaml` pointing at the reference, reusing `SOUL.md` for the system prompt
- In-memory skill dict reloaded — the dropdown picks up the new skill immediately

Returns `{"error": "..."}` if: the slug collides with an existing skill, Whisper produces empty text, Fish rejects the reference, etc.

## `POST /api/offer`

Pipecat `SmallWebRTCTransport` signalling. Accepts the SDP offer, instantiates a `SmallWebRTCConnection`, returns the SDP answer + `pc_id`.

```http
POST /api/offer
Content-Type: application/json

{
  "sdp": "v=0\r\no=...",
  "type": "offer"
}
```

Response:

```json
{
  "sdp": "v=0\r\no=...",
  "type": "answer",
  "pc_id": "SmallWebRTCConnection#0-<uuid>"
}
```

See [WebRTC Protocol](./webrtc-protocol) for the full handshake.

## `PATCH /api/offer`

Trickle ICE candidates. Required after the POST answer returns with a `pc_id`.

```http
PATCH /api/offer
Content-Type: application/json

{
  "pc_id": "SmallWebRTCConnection#0-<uuid>",
  "candidates": [
    {
      "candidate": "candidate:1 1 udp 2113667326 192.168.1.10 58387 typ host",
      "sdp_mid": "0",
      "sdp_mline_index": 0
    }
  ]
}
```

Returns `{"status":"success"}` on accepted, `404` if `pc_id` is unknown (e.g. connection expired).

## `GET /.well-known/agent-card.json`

A2A agent card. Aliased as `/.well-known/agent.json` for legacy callers. Cached one minute (`Cache-Control: public, max-age=60`).

## `POST /a2a`

JSON-RPC 2.0 inbound. Supports `method: "message/send"`. Requires `X-API-Key` or `Authorization: Bearer <A2A_AUTH_TOKEN>` when `A2A_AUTH_TOKEN` is set.

Returns an A2A task result with `artifacts[0].parts[0]` containing the assistant text. See [A2A Integration](/guides/a2a-integration) for the full flow.

## `POST /a2a/callback`

Receives push-notification results from agents we dispatched to. Accepts flexible shapes; looks for text at `body.text`, `body.result.artifacts[].parts[].text`, or `body.message.parts[].text`.

When an active voice session exists, speaks the result via the `DeliveryController` with `next_silence` policy. Otherwise returns `{"ok": true, "delivered": false, "reason": "no active session"}`.

## `GET /static/*`

Static assets from `./static/`. Currently used only for the browser client.
