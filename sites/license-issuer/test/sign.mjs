// Interop bridge for tests/test_license_issuer_interop.py.
//
// Reads {"privateJwk": {...}, "opts": {...}} as JSON on stdin, mints a license
// with the SAME code the Worker uses, and writes the token to stdout. The Python
// side then verifies that token with agent.license.verify_license — proving a
// Worker-minted key activates in the app.

import { readFileSync } from "node:fs";
import { signLicense } from "../src/license.js";

const input = JSON.parse(readFileSync(0, "utf8"));
const { token } = await signLicense(input.opts, input.privateJwk);
process.stdout.write(token);
