// Generate the production Ed25519 license keypair.
//
//   node scripts/gen-keypair.mjs
//
// Writes the PRIVATE key (JWK) to .secrets/license-private.jwk.json (gitignored,
// mode 0600) and prints the PUBLIC key in the two forms the app understands:
//   - raw base64url  → set as ORBIS_LICENSE_PUBKEY (baked into the build)
//   - SPKI PEM       → optional config/license_pubkey.pem replacement
//
// The private key is what the Worker signs licenses with. After generating:
//   1. wrangler secret put LICENSE_PRIVATE_KEY   (paste the .secrets JWK contents)
//   2. store the same JWK in Infisical
//   3. delete the local .secrets copy
// Treat the private key like a signing cert: whoever holds it can mint licenses.

import { mkdir, writeFile } from "node:fs/promises";

const { publicKey, privateKey } = await crypto.subtle.generateKey(
  { name: "Ed25519" },
  true,
  ["sign", "verify"],
);

const privJwk = await crypto.subtle.exportKey("jwk", privateKey);
const pubJwk = await crypto.subtle.exportKey("jwk", publicKey);

// Minimal canonical private JWK the Worker imports.
const privateJwk = { kty: "OKP", crv: "Ed25519", d: privJwk.d, x: privJwk.x };

await mkdir(".secrets", { recursive: true });
await writeFile(
  ".secrets/license-private.jwk.json",
  JSON.stringify(privateJwk) + "\n",
  { mode: 0o600 },
);

// SPKI PEM of the public key (for config/license_pubkey.pem if you prefer a file).
const spki = await crypto.subtle.exportKey("spki", publicKey);
const spkiB64 = Buffer.from(spki).toString("base64");
const pem =
  "-----BEGIN PUBLIC KEY-----\n" +
  (spkiB64.match(/.{1,64}/g) || []).join("\n") +
  "\n-----END PUBLIC KEY-----\n";

console.log("");
console.log("✓ private key written → .secrets/license-private.jwk.json (gitignored)");
console.log("");
console.log("PUBLIC KEY (raw base64url) — set as ORBIS_LICENSE_PUBKEY in the build:");
console.log("");
console.log("  " + pubJwk.x);
console.log("");
console.log("PUBLIC KEY (SPKI PEM) — optional config/license_pubkey.pem replacement:");
console.log("");
console.log(pem);
console.log("Next:");
console.log("  wrangler secret put LICENSE_PRIVATE_KEY   # paste .secrets/license-private.jwk.json");
console.log("  (then store the JWK in Infisical and delete the local .secrets copy)");
