// Ed25519 license minting.
//
// The tokens produced here MUST verify in the ORBIS app's offline verifier,
// `agent/license.py::verify_license`. That function:
//   1. strips the "ORBIS-" prefix,
//   2. splits "<body_seg>.<sig_seg>" on the first ".",
//   3. verifies the Ed25519 signature over the ASCII bytes of `body_seg`,
//   4. json.loads(b64url_decode(body_seg)) and requires payload.v == 1.
//
// Critically, the Python side verifies the signature against the *received*
// body segment string — it does NOT re-serialize the JSON — so we are free to
// serialize the payload however we like, as long as the bytes we b64url-encode
// into `body_seg` are exactly the bytes we sign over. The interop regression
// test (tests/test_license_issuer_interop.py) pins this contract.
//
// Web Crypto is used throughout so the exact same module runs unmodified in a
// Cloudflare Worker and under Node 20+ (for the test harness).

const TOKEN_PREFIX = "ORBIS-";
const FEATURE = "customization";
const VERSION = 1;

const enc = new TextEncoder();

/** base64url (no padding) from raw bytes / ArrayBuffer. */
export function b64url(input) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Import an Ed25519 private key (JWK form: {kty:'OKP',crv:'Ed25519',d,x}) as a
 * non-extractable signing key.
 */
async function importSigningKey(privateJwk) {
  const jwk = {
    kty: privateJwk.kty,
    crv: privateJwk.crv,
    d: privateJwk.d,
    x: privateJwk.x,
  };
  return crypto.subtle.importKey("jwk", jwk, { name: "Ed25519" }, false, [
    "sign",
  ]);
}

/**
 * Mint a signed ORBIS license token.
 *
 * @param {{ sub: string, lid?: string, iat?: number, feat?: string }} opts
 *        `sub` is the buyer identity (email) shown as "Licensed to …".
 * @param {JsonWebKey} privateJwk  Ed25519 private key in JWK form.
 * @returns {Promise<{ token: string, payload: object }>}
 */
export async function signLicense(opts, privateJwk) {
  if (!opts || !opts.sub) throw new Error("signLicense: opts.sub is required");
  const payload = {
    v: VERSION,
    feat: opts.feat || FEATURE,
    iat: opts.iat ?? Math.floor(Date.now() / 1000),
    lid: opts.lid || crypto.randomUUID(),
    sub: opts.sub,
  };
  const bodySeg = b64url(enc.encode(JSON.stringify(payload)));
  const key = await importSigningKey(privateJwk);
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, key, enc.encode(bodySeg));
  return { token: `${TOKEN_PREFIX}${bodySeg}.${b64url(sig)}`, payload };
}

export { FEATURE, VERSION, TOKEN_PREFIX };
