# Users & API keys

protoVoice identifies each caller by an API key. Keys resolve to **users** in a roster loaded from Infisical (primary) or a local YAML file (fallback). Every `/api/*` route requires one of:

```http
X-API-Key: <key>
Authorization: Bearer <key>
```

Keys are compared in constant time against the sha256 of each roster entry's `api_key`. Clients never see other users' keys — only the resolved user id + display name via [`GET /api/whoami`](../reference/http-api#get-api-whoami).

## Single-user fallback (the default)

When the roster is empty — no Infisical secret and no `config/users.yaml` — every request resolves to a synthetic user:

```json
{ "id": "default", "display_name": "Default", "auth_source": "empty" }
```

No auth is enforced. Keeps local dev + existing tailnet-only deployments working unchanged. The moment the registry has ≥1 real user, auth enforcement kicks in and unknown keys return `401`.

## Roster shape

```yaml
# config/users.yaml
users:
  - id: alice
    api_key: pv_ak_aXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
    display_name: Alice
    role: user                    # default — can be omitted
    allowed_skills: [josh, chef]  # Alice's dropdown is filtered to these
    pinned_viz:                   # override the active skill's default viz
      palette: Noir

  - id: carol
    api_key: pv_ak_cZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ
    display_name: Carol
    allowed_skills: [josh]        # single-element list → read-only chip in UI

  - id: bob
    api_key: pv_ak_bYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY
    display_name: Bob
    role: admin                   # free to pick any skill, edit orb viz, edit other users
```

| Field | Required | Purpose |
|:---|:---|:---|
| `id` | yes | Short, URL-safe identifier. Used in session-memory paths (`/tmp/protovoice_sessions/{id}/…`) and Langfuse attribution. |
| `api_key` | yes | Secret the client sends. Generate with `python3 -c "import secrets; print('pv_ak_' + secrets.token_urlsafe(32))"`. |
| `display_name` | no | Human-readable name for UI. Defaults to `id` title-cased. |
| `role` | no | `user` (default) or `admin`. Users are constrained by `allowed_skills`; admins are unconstrained. |
| `allowed_skills` | no | List of skill slugs this user can activate. Omit for no constraint (full catalog). A single-element list locks the user to that skill — `POST /api/skills` returns `403` for anything outside the list. Admins ignore this field. |
| `pinned_viz` | no | Mapping with optional `variant` / `palette` / `params`. Overrides the active skill's viz block on session start. Applies to any role. |

Copy `config/users.example.yaml` as a starting point.

### Roles

| Role | Can change own skill | Can edit orb viz | Can edit other users |
|:---|:---|:---|:---|
| `user` (default) | within `allowed_skills` (or any, if unset) | only if no `pinned_viz` and `allowed_skills` isn't a single-element list | no |
| `admin` | yes, freely | yes, freely | yes (via `POST /api/admin/skills`) |

The single-user fallback (empty roster) resolves every request to a synthetic `default` user with `role: admin` — local dev + tailnet-only deployments stay unconstrained.

### Skill access control

`allowed_skills` is how multi-tenant installs steer each user to a curated set of personas:

- `allowed_skills: [josh, chef]` filters Alice's `/api/skills` dropdown to those two slugs. She can switch freely between them but `POST /api/skills` with any other slug returns `403`.
- `allowed_skills: [josh]` (single entry) collapses the dropdown to a read-only chip labelled "Pinned by admin". `GET /api/skills` returns `locked: true` and `active: "josh"` regardless of mutable state.
- Omitting `allowed_skills` (or setting it to an empty list, which logs a warning and is treated as omitted) gives the user access to the full catalog.
- If the user's stored active skill falls outside a newly narrowed `allowed_skills`, `GET /api/skills` snaps `active` to the first allowed slug — they're never stuck on a skill they can't activate.
- Admins ignore `allowed_skills` entirely. Setting it on an admin entry is a no-op.
- `config/users.yaml` is authoritative. There is no runtime API to mutate the access list yet — edit the YAML (or the `USERS_YAML` Infisical secret) and call `POST /api/users/reload`. A full admin CRUD UI is future work.

### Admin overrides

Admins can set any user's mutable active skill without editing the roster:

```bash
curl -X POST https://protovoice/api/admin/skills \
  -H "X-API-Key: <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "alice", "slug": "chef"}'
```

This updates the target user's in-memory `UserState.skill_slug` (applies on their next connect). It does **not** modify `allowed_skills` — for persistent access-list changes, edit the roster. Admin writes to `config/users.yaml` from the UI are planned; for now, administer the roster out-of-band.

## Infisical roster (recommended for the fleet)

Store a single Infisical secret named `USERS_YAML` whose value is the full YAML content above. protoVoice fetches and parses it at boot (and on `POST /api/users/reload`).

Env vars:

| Variable | Purpose |
|:---|:---|
| `INFISICAL_API_URL` | Base URL (default `https://app.infisical.com`; point at self-hosted pve01 instance) |
| `INFISICAL_CLIENT_ID` + `INFISICAL_CLIENT_SECRET` | Machine-identity credentials (universal-auth) |
| `INFISICAL_PROJECT_ID` | Workspace/project id |
| `INFISICAL_ENVIRONMENT` | Env slug, default `prod` |
| `INFISICAL_SECRET_PATH` | Folder path, default `/protovoice` |
| `INFISICAL_USERS_SECRET_NAME` | Secret name, default `USERS_YAML` |

When all three credential vars are set, Infisical becomes the active source; `config/users.yaml` is ignored. `GET /api/whoami.auth_source` reports `"infisical"` in that case.

**Rationale for a single YAML blob** rather than one secret per user: atomic updates, exact parity with the on-disk file, and `POST /api/users/reload` re-parses in one shot.

## What's scoped per-user

| Concern | Behavior |
|:---|:---|
| Skill selection (`/api/skills`) | Each user has their own active skill — Alice on `chef` doesn't affect Bob's dropdown. Non-admins are filtered to their `allowed_skills`; disallowed slugs return `403`. |
| Orb viz | `pinned_viz` on the user entry overrides `skill.viz`. Non-admin users can't open the Orb settings tab in the drawer. |
| Verbosity (`/api/verbosity`) | Per-user. Alice's `silent` is invisible to Bob's `chatty`. |
| Session memory | Stored at `{SESSION_STORE_DIR}/{user_id}/{skill_slug}.txt` — no cross-user sharing by design. |
| Stashed deliveries | Same path pattern. Replays only to the user who was offline when the push arrived. |
| Active DeliveryController / Langfuse tracer | Each concurrent user gets their own; no singleton cross-contamination. |
| Langfuse spans | Stamped with `user_id` + `session_id` so you can filter traces by user. |

## What's still process-global

- `/config/delegates.yaml` — same registry for everyone; each skill filters via `skill.delegates: [...]`.
- `/config/skills/*.yaml` — same catalog for everyone; users pick one at a time.
- `LLM_URL` / `TTS_BACKEND` / `STT_BACKEND` env defaults — per-skill overrides via `skill.llm` take priority.

## A2A inbound

Inbound A2A traffic (`POST /a2a`) is gated separately by `A2A_AUTH_TOKEN`. Stashed deliveries + skill attribution for the inbound path resolve to the user named by `A2A_USER_ID` (default `default`). True per-caller A2A auth — each fleet agent holding its own key against the same users roster — is future work.

## Reloading the roster

```bash
curl -X POST https://protovoice/api/users/reload \
  -H "X-API-Key: <your-key>"
# → {"ok": true, "users": ["alice", "bob"], "source": "infisical"}
```

Safe mid-session. Active clients keep their authenticated state until they reconnect; new connections see the refreshed registry.

## Implementation

- `auth/users.py` — UserRegistry, sha256 lookup, FastAPI `require_user` dependency
- `auth/infisical.py` — universal-auth login + single-secret fetch
- `auth/context.py` — `current_user_id` / `current_session_id` ContextVars
- `agent/user_state.py` — `UserState` dataclass + registry, lazy FillerGenerator per user

Each of the pieces above is importable in isolation; auth can be swapped for a different source (OAuth, header-stamping reverse-proxy, etc.) by replacing the UserRegistry's source.
