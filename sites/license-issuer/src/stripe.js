// Minimal Stripe client — raw fetch + Web Crypto, no SDK. Keeps the Worker
// bundle tiny and dependency-free, and keeps webhook verification fully owned.

const STRIPE_API = "https://api.stripe.com/v1";

/** Create a one-time Checkout Session for the customization unlock. */
export async function createCheckoutSession(env, { successUrl, cancelUrl }) {
  const form = new URLSearchParams();
  form.set("mode", "payment");
  form.set("line_items[0][price]", env.STRIPE_PRICE_ID);
  form.set("line_items[0][quantity]", "1");
  form.set("success_url", successUrl);
  form.set("cancel_url", cancelUrl);
  form.set("customer_creation", "always");
  // Tag the session so the webhook can sanity-check what it's fulfilling.
  form.set("metadata[feat]", "customization");
  form.set("payment_intent_data[metadata][feat]", "customization");
  // Only request automatic tax once Stripe Tax is enabled on the account,
  // otherwise Checkout creation errors. Flip STRIPE_AUTOMATIC_TAX=true then.
  if (String(env.STRIPE_AUTOMATIC_TAX || "").toLowerCase() === "true") {
    form.set("automatic_tax[enabled]", "true");
    form.set("billing_address_collection", "required");
  }

  const resp = await fetch(`${STRIPE_API}/checkout/sessions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.STRIPE_SECRET_KEY}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: form.toString(),
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(`stripe checkout create failed: ${data?.error?.message || resp.status}`);
  }
  return data; // { id, url, ... }
}

const encoder = new TextEncoder();

function hex(buf) {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Constant-time string compare (equal-length hex). */
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/**
 * Verify a Stripe webhook signature and return the parsed event.
 * Mirrors Stripe's scheme: signedPayload = `${t}.${rawBody}`, expected =
 * HMAC-SHA256(signedPayload, secret), compared to the `v1` header signature,
 * with a timestamp-tolerance check to block replays.
 *
 * @param {string} rawBody  exact request body bytes as text
 * @param {string} sigHeader  the `Stripe-Signature` header
 * @param {string} secret  STRIPE_WEBHOOK_SECRET (whsec_…)
 * @param {number} toleranceSec
 */
export async function verifyWebhook(rawBody, sigHeader, secret, toleranceSec = 300) {
  if (!sigHeader) throw new Error("missing Stripe-Signature header");
  const parts = Object.fromEntries(
    sigHeader.split(",").map((kv) => {
      const i = kv.indexOf("=");
      return [kv.slice(0, i).trim(), kv.slice(i + 1).trim()];
    }),
  );
  const t = parts.t;
  const v1 = parts.v1;
  if (!t || !v1) throw new Error("malformed Stripe-Signature header");

  const nowSec = Math.floor(Date.now() / 1000);
  if (Math.abs(nowSec - Number(t)) > toleranceSec) {
    throw new Error("webhook timestamp outside tolerance");
  }

  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, encoder.encode(`${t}.${rawBody}`));
  if (!timingSafeEqual(hex(mac), v1)) {
    throw new Error("webhook signature mismatch");
  }
  return JSON.parse(rawBody);
}
