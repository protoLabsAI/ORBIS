# Status — pickup for next session

Updated after v0.12.1. Branch: `main`.

## TL;DR

Multi-tenant Phase 1.5 shipped across v0.11.0 → v0.12.1: API-key auth, per-user state, role-based access (`admin` vs `user`), skill-access control via `allowed_skills`, per-user `pinned_viz`, and dedicated orb viz per skill. First test suite in the repo landed with v0.12.1 (33 tests, pytest). Running at `https://protolabs.taild25506.ts.net/`; containers at `ghcr.io/protolabsai/protovoice:v0.12.1` + `:latest`.

## Where we are

### Deployed
- `https://protolabs.taild25506.ts.net/` (tailnet-only, ava Blackwell node, Fish S2-Pro TTS, Qwen 3.6-35B via host vLLM on `:8000`).
- Docker images: `ghcr.io/protolabsai/protovoice:v0.12.1`, `:0.12`, `:latest`.
- Docs: `https://protolabsai.github.io/protoVoice/` — pages rebuilt on every main push.

### Recent releases
- **v0.12.1** ([release](https://github.com/protoLabsAI/protoVoice/releases/tag/v0.12.1)) — greenfield rename `pinned_skill` → `allowed_skills: list[str]`. Filtered dropdown for non-admins; single-element list keeps the read-only-chip behavior. First test suite (`tests/`, 33 passing — unit + TestClient integration).
- **v0.12.0** ([release](https://github.com/protoLabsAI/protoVoice/releases/tag/v0.12.0)) — role-based access (`admin` vs `user`), `pinned_viz`, `POST /api/admin/skills`, per-skill `viz:` block with client auto-apply.
- **v0.11.0** — API-key auth, Infisical/YAML roster, per-user skill/verbosity/delivery/tracer/filler state, session memory at `{SESSION_STORE_DIR}/{user_id}/{skill_slug}.txt`, Langfuse spans stamped with `user_id` + `session_id`.

### What's in the multi-tenant model today

| Layer | Behavior |
|:---|:---|
| Auth | `X-API-Key` or `Authorization: Bearer`; roster from Infisical (primary) → `config/users.yaml` (fallback) → empty = single-user fallback (synthetic `default` user, runs as admin). |
| Roles | `user` (default, constrained) vs `admin` (unconstrained, can edit other users via `POST /api/admin/skills`). |
| Skill access | `allowed_skills: [a, b]` on a user entry filters `/api/skills` and 403s disallowed slugs on POST. Single-element list → read-only chip in UI. Omit for unconstrained. Admins ignore it. |
| Orb viz | `skill.viz` (variant + palette + params) applies on skill switch; `user.pinned_viz` on the roster overrides. Non-admins don't see the Orb tab in the drawer. |
| Per-user state | Skill, verbosity, delivery controller, tracer, filler state, session memory paths — all keyed by `user.id`. ContextVars carry `current_user_id` / `current_session_id` across async boundaries. |
| Admin API | `POST /api/admin/skills` to set any user's mutable active skill. No runtime pin mutation API yet — edit the YAML + `POST /api/users/reload` for persistent access-list changes. |

### What's still deferred

| Item | Why deferred | Notes |
|---|---|---|
| Admin CRUD UI (add/edit/remove users from drawer) | Scope — YAML-edit-and-reload works today | Needs a writable roster backend + admin-only drawer tab |
| Frontend API-key paste field + 401 handling | Single-user fallback keeps dev unblocked | Small lift; gate on when the tailnet installs multiple users |
| True per-caller A2A inbound auth | Shared `A2A_AUTH_TOKEN` + `A2A_USER_ID` default works for the fleet | Land when A2A goes public-exposed |
| `/a2a/push` target-user via per-session token | Same as above | |
| `GH_PAT` secret on the repo | Prevents `prepare-release.yml` from auto-running on PR merge | Manual tag cut works today (commit → push → annotated tag → push tag); re-enable the workflow by adding the secret |
| Prometheus / HF Spaces / E2E | Observability + deploy polish | v0.13+ |
| Collectible orbs + MTX | Scope moved to a separate app | Not a protoVoice feature |

## Tests

First suite landed in v0.12.1.

```bash
# From repo root:
.venv/bin/python -m pytest              # 33 passing, ~3s
```

- `tests/test_users.py` — 17 unit tests for `auth/users.py`: `User.allows_skill()` truth table, YAML parsing edge cases (empty list, non-list, stripped/dropped entries, unknown role, malformed pinned_viz), `by_id` lookup, reload flow.
- `tests/test_endpoints.py` — 16 FastAPI TestClient integration tests: `/api/whoami` shape, `/api/skills` filtering + `locked` flag, active-slug drift-to-first-allowed, POST permit/deny paths, admin-only `/api/admin/skills`.
- `pyproject.toml` has `[tool.pytest.ini_options]` pointing at `tests/`; `audioop` DeprecationWarning filtered (pipecat imports it on Python 3.12).

No CI runner wired yet — tests run locally. Adding a `pytest.yml` workflow is the obvious next step if multi-person collaboration picks up.

## Waiting on external

### Workstacean
1. **F7 ava delegate flip** — code-complete on our side. Held at `type: openai` pending [protoWorkstacean#471](https://github.com/protoLabsAI/protoWorkstacean/issues/471): (a) `message/send` routes to protoBot instead of ava, (b) `message/stream` intermittently returns `Cannot POST /` 404. When resolved, edit `config/delegates.yaml`, swap the commented a2a block for the openai one.
2. **Tracing contract implementation** — [`docs/reference/tracing-contract.md`](https://protolabsai.github.io/protoVoice/reference/tracing-contract/) defines `Langfuse-Trace-Id` / `Langfuse-Session-Id` / `Langfuse-Parent-Observation-Id`. Until they implement "continue, don't create" in their `/a2a` handler, headers attach but traces don't stitch across the fleet.

### Langfuse config
Not wired yet — code fails open when `LANGFUSE_*` env is unset. When ready:
```
LANGFUSE_HOST=http://ava:3000
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```
In `.env` or the deployment env. No code changes needed.

## Repo layout (quick reference)

- `auth/` — UserRegistry, Infisical fetch, require_user / require_admin, ContextVars
- `agent/` — per-user state, tracing, session_store, filler generator, delivery controller
- `skills/` — YAML loader with `extends:` inheritance, models
- `app.py` — FastAPI routes, RTVI wiring, Pipecat pipeline builder
- `a2a/` — inbound + outbound A2A (JSON-RPC, `message/send` + `message/stream`)
- `web/` — bun + Vite 6 + React 19 + shadcn/ui + Tailwind 4, PWA
  - `src/voice/` — Pipecat client wiring + derived voice-state store
  - `src/plugins/{orb,orb-settings,status-chip,status-pill,voice-panel}` — each registers via `registerPlugin({ id, slots })`
  - `src/auth/` — whoami store + selectors (`isAdmin`, `isSkillLocked`, `isVizLocked`)
  - `src/lib/api.ts` — typed fetches for `/api/*`
- `config/skills/*.yaml` — skill catalog (`viz:` block per skill optional)
- `config/users.yaml` — roster (with `allowed_skills`, `pinned_viz`, `role`); copy `users.example.yaml`
- `tests/` — pytest suite (users + endpoints)

## Known tripwires (things NOT to change lightly)

- **Don't touch getUserMedia constraints.** Disabling AGC/NS/EC breaks server VAD.
- **Don't reintroduce a hand-rolled memory pruner.** Pipecat's `LLMContextSummarizer` is the right primitive.
- **Don't remove `messageId`** from outbound A2A calls. Spec-required; workstacean enforces.
- **Don't forget `uTime` + `orb.rotation` wrap** — GLSL float32 precision degrades after ~10 min; wrap at 2π·N.
- **Don't touch `append_to_context=False`** on filler / backchannel / delivery `TTSSpeakFrame`s — without it the LLM riffs on its own fillers.
- **`DEFAULT_USER` is admin on purpose.** Single-user fallback keeps dev unconstrained; don't "fix" it to `user` without adding a way out.

## Quick-start

```bash
cd ~/dev/protoVoice
git status && git log --oneline -5

# Health + smoke
curl -sS https://protolabs.taild25506.ts.net/healthz | jq .

# Tests
.venv/bin/python -m pytest

# Release flow (manual — GH_PAT not set on repo)
python3 scripts/version.py {patch|minor|major}
git add pyproject.toml && git commit -m "chore: release v<x.y.z>"
git tag -a v<x.y.z> -m "<annotation>" HEAD
git push origin main && git push origin v<x.y.z>
# → release.yml builds semver images + creates GH release
# → docker-publish.yml refreshes :latest on main push
```

## One-line rollback

```bash
git checkout v0.11.0    # last pre-roles tag
docker compose up -d --no-deps --force-recreate protovoice
```
