# orbis-license-issuer

Cloudflare Worker that owns the ORBIS paid-unlock pipeline end to end:

```
Buyer ─▶ Stripe Checkout ($9 one-time) ─▶ /webhook (this Worker)
                                            • verify Stripe signature
                                            • mint an Ed25519 license  (private key = Worker secret)
                                            • store in KV + email the key (Resend)
Buyer pastes key ─▶ ORBIS  POST /api/entitlement/activate ─▶ offline verify ─▶ unlocked (perpetual)
```

The license token format and the app-side verifier live in `agent/license.py`;
the mint code here (`src/license.js`) is pinned to it by
`tests/test_license_issuer_interop.py` (a license minted here must verify there).

No SDKs — Stripe, the webhook HMAC, Ed25519, and email are all raw `fetch` +
Web Crypto, so the bundle is tiny and the stack is fully self-owned.

## Endpoints

| Route | Method | Purpose |
|---|---|---|
| `/checkout` | POST | Create a Checkout Session, returns `{ url }` to open. |
| `/webhook` | POST | Stripe webhook → verify → mint → store → email. |
| `/license?session_id=…` | GET | `{ token }` once minted (in-app polling), else `{ pending: true }`. |
| `/success?session_id=…` | GET | Human page showing the key + paste steps. |
| `/health` | GET | `{ ok: true }`. |

## One-time setup

### 1. Keypair
```bash
npm install
npm run gen-keypair
```
Writes the **private** key to `.secrets/license-private.jwk.json` (gitignored)
and prints the **public** key. Bake the public key into the app build (set
`ORBIS_LICENSE_PUBKEY` to the raw base64url value — see the desktop build).
Store the private JWK in Infisical, set it as a Worker secret (below), then
delete the local `.secrets` copy.

### 2. KV namespace
```bash
wrangler kv namespace create LICENSES
```
Paste the returned `id` into `wrangler.toml`.

### 3. Stripe
- Create a **one-time Price of $9** (Product: "ORBIS customization unlock").
  Put its `price_…` id in `wrangler.toml` → `STRIPE_PRICE_ID`.
- Add a **webhook endpoint** pointing at `https://<worker-url>/webhook`,
  subscribed to **`checkout.session.completed`**. Copy its signing secret
  (`whsec_…`).
- (Optional, recommended at launch) enable **Stripe Tax**, then set
  `STRIPE_AUTOMATIC_TAX = "true"` in `wrangler.toml`. You are the merchant of
  record — register + remit where you have nexus.

### 4. Resend (email delivery — optional)
Verify a sending domain, create an API key. If unset, buyers still get the key
on the success page.

### 5. Secrets
```bash
wrangler secret put STRIPE_SECRET_KEY
wrangler secret put STRIPE_WEBHOOK_SECRET
wrangler secret put LICENSE_PRIVATE_KEY     # paste .secrets/license-private.jwk.json
wrangler secret put RESEND_API_KEY          # optional
```

### 6. Deploy
```bash
npm run deploy
```
Or push to `main` — `.github/workflows/license-issuer-deploy.yml` deploys on
changes under `sites/license-issuer/**` (needs `CLOUDFLARE_API_TOKEN` +
`CLOUDFLARE_ACCOUNT_ID` repo secrets, already used by the marketing deploy).

## Local dev
```bash
cp .dev.vars.example .dev.vars   # fill with Stripe TEST keys + a test keypair
npm run dev
stripe listen --forward-to localhost:8787/webhook   # Stripe CLI for webhooks
```

## Test the interop contract
```bash
# from the repo root
.venv/bin/python -m pytest tests/test_license_issuer_interop.py -q
```
