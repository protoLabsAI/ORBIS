// ORBIS license-issuer — Cloudflare Worker.
//
// Owns the paid-unlock pipeline end to end:
//   POST /checkout            → create a Stripe Checkout Session ($9 one-time)
//   POST /webhook             → Stripe webhook: verify sig → mint Ed25519 license
//                               → store in KV → email the key (idempotent)
//   GET  /license?session_id  → JSON { token } once minted (for in-app polling)
//   GET  /success?session_id  → human page that shows the key + paste steps
//   GET  /health              → ok
//
// Secrets (wrangler secret put):  STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
//   LICENSE_PRIVATE_KEY (Ed25519 JWK), RESEND_API_KEY
// Vars (wrangler.toml):  STRIPE_PRICE_ID, SUCCESS_URL, CANCEL_URL, EMAIL_FROM,
//   ALLOW_ORIGIN, STRIPE_AUTOMATIC_TAX
// KV binding:  LICENSES

import { signLicense } from "./license.js";
import { createCheckoutSession, verifyWebhook } from "./stripe.js";
import { sendLicenseEmail } from "./email.js";

const json = (obj, status = 200, extra = {}) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", ...extra },
  });

function corsHeaders(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOW_ORIGIN || "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

const kvKey = (sessionId) => `session:${sessionId}`;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const { pathname } = url;
    const cors = corsHeaders(env);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    if (pathname === "/health") return json({ ok: true }, 200, cors);

    // --- Create a checkout session ---------------------------------------
    if (pathname === "/checkout" && request.method === "POST") {
      try {
        const session = await createCheckoutSession(env, {
          successUrl:
            (env.SUCCESS_URL || `${url.origin}/success`) +
            "?session_id={CHECKOUT_SESSION_ID}",
          cancelUrl: env.CANCEL_URL || `${url.origin}/`,
        });
        return json({ url: session.url, id: session.id }, 200, cors);
      } catch (err) {
        console.log(`[checkout] ${err.message}`);
        return json({ error: "checkout_failed" }, 502, cors);
      }
    }

    // --- Stripe webhook → mint + deliver ---------------------------------
    if (pathname === "/webhook" && request.method === "POST") {
      const raw = await request.text();
      let event;
      try {
        event = await verifyWebhook(
          raw,
          request.headers.get("Stripe-Signature"),
          env.STRIPE_WEBHOOK_SECRET,
        );
      } catch (err) {
        console.log(`[webhook] signature rejected: ${err.message}`);
        return new Response("invalid signature", { status: 400 });
      }

      if (event.type === "checkout.session.completed") {
        const session = event.data.object;
        if (session.payment_status === "paid") {
          // Do the slow work without blocking the 200 Stripe needs fast.
          ctx.waitUntil(fulfill(env, session));
        }
      }
      return new Response("ok", { status: 200 });
    }

    // --- Poll for the minted key (in-app flow) ---------------------------
    if (pathname === "/license") {
      const sessionId = url.searchParams.get("session_id");
      if (!sessionId) return json({ error: "session_id required" }, 400, cors);
      const stored = await env.LICENSES.get(kvKey(sessionId), "json");
      if (!stored) return json({ pending: true }, 200, cors);
      return json({ token: stored.token }, 200, cors);
    }

    // --- Human success page ----------------------------------------------
    if (pathname === "/success") {
      const sessionId = url.searchParams.get("session_id") || "";
      const stored = sessionId
        ? await env.LICENSES.get(kvKey(sessionId), "json")
        : null;
      return new Response(successHtml(stored?.token), {
        status: 200,
        headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }

    return json({ error: "not_found" }, 404, cors);
  },
};

/** Mint the license for a paid session, store it, and email it. Idempotent. */
async function fulfill(env, session) {
  const sessionId = session.id;
  const existing = await env.LICENSES.get(kvKey(sessionId), "json");
  if (existing) return; // already fulfilled — Stripe re-delivered the event

  const email =
    session.customer_details?.email || session.customer_email || "buyer";
  const privateJwk = JSON.parse(env.LICENSE_PRIVATE_KEY);
  const { token, payload } = await signLicense({ sub: email }, privateJwk);

  await env.LICENSES.put(
    kvKey(sessionId),
    JSON.stringify({ token, email, lid: payload.lid, created: payload.iat }),
  );
  console.log(`[fulfill] minted lid=${payload.lid} for session=${sessionId}`);

  try {
    await sendLicenseEmail(env, { to: email, token });
  } catch (err) {
    console.log(`[fulfill] email error (key still on success page): ${err.message}`);
  }
}

function successHtml(token) {
  const body = token
    ? `<p>Here's your license key — paste it into ORBIS under
         <strong>Settings → Unlock customization</strong>:</p>
       <pre>${token}</pre>
       <p class="muted">We also emailed it to you. It's a one-time, perpetual
         unlock — keep it to re-activate on any machine.</p>`
    : `<p>Your payment went through — we're generating your license key now.</p>
       <p class="muted">This page will refresh automatically; the key also
         arrives by email within a minute.</p>
       <script>setTimeout(function(){location.reload()},4000)</script>`;
  return `<!doctype html><html><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>ORBIS — unlock</title>
    <style>
      body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#09090b;color:#e4e4e7;
        display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:24px}
      .card{max-width:560px}
      h1{color:#818cf8;font-size:22px}
      pre{background:#18181b;border:1px solid #27272a;border-radius:10px;padding:16px;
        white-space:pre-wrap;word-break:break-all;font-size:13px;color:#a5b4fc}
      .muted{color:#71717a;font-size:14px}
    </style></head>
    <body><div class="card"><h1>Thanks for supporting ORBIS</h1>${body}</div></body></html>`;
}
