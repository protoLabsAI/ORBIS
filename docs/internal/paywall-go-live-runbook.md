# Paywall go-live runbook

**Status:** the paid-unlock system is fully built but **dormant** — the gate is
open in production, so customization is free everywhere it's reachable. The
*only* reason it isn't enforced: the production license public key
(`ORBIS_LICENSE_PUBKEY`) has never been set, so `option_env!` is empty and the
Rust shell leaves `ORBIS_GATE` at its default (`open`). This runbook flips it on.

Do NOT flip this on until the orb editor is also re-exposed for paid users
(step 4) — otherwise there's a paywall with nothing behind it.

## How it works (already built)

```
Buyer ─▶ Stripe Checkout ($9 one-time) ─▶ Worker /webhook
                                            • verify Stripe HMAC
                                            • mint Ed25519 license (private key = Worker secret)
                                            • store in KV + email (Resend)
Buyer pastes key ─▶ ORBIS POST /api/entitlement/activate ─▶ offline verify ─▶ unlocked (perpetual)
```

- **App verifier:** `agent/license.py` (Ed25519 verify) + `agent/entitlement.py`
  (`ORBIS_GATE` open|closed, `has_customization()`). Gate defaults to `open`.
- **Gate wiring:** `src-tauri/src/lib.rs` ~1692 — *if* `option_env!("ORBIS_LICENSE_PUBKEY")`
  is non-empty at build time, the shell passes `ORBIS_GATE=closed` **and** the
  pubkey to the sidecar. So **setting the one build variable closes the gate AND
  turns on verification.** Empty/unset → gate open (can't strand users with no
  unlock path).
- **Issuer:** `sites/license-issuer/` — Cloudflare Worker (Stripe → mint → KV →
  email), raw `fetch` + Web Crypto, no SDKs. Interop pinned by
  `tests/test_license_issuer_interop.py` (a key minted there must verify in
  `agent/license.py`).
- **Editor gate (frontend):** `web/src/plugins/orb-settings/OrbSettingsPanel.tsx`
  already checks `useEntitlement()` → `ent.locked` → renders
  `UnlockCustomization` (the buy CTA) when locked, the full editor when licensed.
  The free starter pool (`config/starter_orbs.yaml`) is always free.

## Go-live steps

### 1. Generate the production keypair
```bash
cd sites/license-issuer
npm install
npm run gen-keypair        # → node scripts/gen-keypair.mjs
```
Writes the **private** JWK to `.secrets/license-private.jwk.json` (gitignored,
0600) and prints the **public** key as raw base64url. Treat the private key like
a signing cert — whoever holds it can mint licenses.

### 2. Set the public key as a repo **Variable** (not a Secret)
It's a *public* key. GitHub → repo (or org) → Settings → Secrets and variables →
Actions → **Variables** → New variable:
```
ORBIS_LICENSE_PUBKEY = <the raw base64url public key from step 1>
```
`desktop-build.yml` / the release build read this into `option_env!`. The **next
release** built after this is set will ship with the gate **closed**. (Confirm
the workflow passes the var into the cargo build env — grep `ORBIS_LICENSE_PUBKEY`
in `.github/workflows/desktop-build.yml`; wire it if missing.)

### 3. Stand up the issuer Worker (`sites/license-issuer/README.md` is authoritative)
```bash
cd sites/license-issuer
wrangler kv namespace create LICENSES      # paste id → wrangler.toml
# Stripe: create a one-time $9 Price → STRIPE_PRICE_ID in wrangler.toml;
#         add webhook → https://<worker>/webhook, event checkout.session.completed
wrangler secret put STRIPE_SECRET_KEY
wrangler secret put STRIPE_WEBHOOK_SECRET
wrangler secret put LICENSE_PRIVATE_KEY    # paste .secrets/license-private.jwk.json
wrangler secret put RESEND_API_KEY         # optional (else key shown on success page)
npm run deploy                             # or push sites/license-issuer/** to main
```
Then store the private JWK in Infisical and **delete the local `.secrets` copy**.
Point the in-app "unlock" CTA / `/checkout` at the deployed Worker URL.

### 4. Re-expose the orb editor for paid users (frontend)
The Orb tab is currently `DEVTOOLS`-gated (dev builds only — see
`web/src/shared/devMode.ts`; this is the post-bypass safe state). At go-live,
show it to everyone so the editor's own entitlement check is the purchase
funnel, while keeping the **Dev tab + premium-orb picker dev-only**.

In `web/src/components/Drawer.tsx`, gate the **Orb** tab on the entitlement
gate, not dev mode — recommended: surface a `gateMode` ('open'|'closed') /
`paywallLive` from `useEntitlement()` and show the Orb tab when
`paywallLive || DEVTOOLS_ENABLED`. So when step 2 closes the gate, the tab
appears automatically; free users get `UnlockCustomization`, paid users the
editor. Leave `{devMode && <TabsTrigger value="dev">}` and the
`BetaOrbsPanel` exactly as-is (those stay dev-only). Update the `grid-cols-*`
count + the `effectiveTab` fallback accordingly.

### 5. Cut a release + verify
- Merge + tag as usual ([[../internal]] / `reference_release_flow`). The build
  bakes `ORBIS_LICENSE_PUBKEY` → ships gate-closed.
- On a CLEAN install (no license): the Orb tab shows the **unlock CTA**, not the
  editor; the free starter switcher still works.
- Buy via Stripe (or mint a test license) → paste the key → editor unlocks and
  stays unlocked (perpetual).
- `curl /healthz`-style: the sidecar entitlement endpoint should report
  `gate_mode: "closed"`.

## Verify the gate locally before shipping
A normal dev build is gate-open. To dry-run gate-closed locally, build with the
pubkey in the env so `option_env!` picks it up:
```bash
ORBIS_LICENSE_PUBKEY="<pubkey>" ./scripts/nuke-and-rebuild.sh --launch
# expect: sidecar log "ORBIS_GATE=closed (paid-unlock gate active)"; editor shows the unlock CTA
```

## Rollback
Delete the `ORBIS_LICENSE_PUBKEY` repo Variable and cut a release — builds revert
to gate-open (empty `option_env!` is treated as unset on purpose). Existing
activated licenses simply stop being required.

## Gotchas
- **Variable, not Secret.** `ORBIS_LICENSE_PUBKEY` is public; storing it as a
  Secret also works but Variables are the right semantic and are visible to
  forks/PRs without leaking anything.
- **Empty == unset.** lib.rs filters empty strings, so a defined-but-blank var
  won't half-close the gate and strand users.
- **Interop is pinned.** Never change the token format on one side only —
  `tests/test_license_issuer_interop.py` must pass (a Worker-minted key verifies
  in `agent/license.py`).
- **Private key is the crown jewel.** Worker secret + Infisical only; never in
  the repo. Rotating it invalidates all issued licenses.
- The main repo stays **private**; only the DMGs are public on `orbis-releases`.
  The paywall is what keeps customization paid while the app is a free download.
