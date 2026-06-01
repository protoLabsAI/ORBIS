# Desktop code signing — operator guide

ORBIS's desktop installers are built by `.github/workflows/
desktop-build.yml` on every semver tag. The current production target
is macOS: semver tag releases require the Apple signing/notarization
secrets and fail fast if any are missing. Manual workflow dispatches
can still produce unsigned `.dmg` artifacts for development/testing.
Linux and Windows signing are intentionally deferred until those
desktop builds come back into the release matrix.

This page covers: which secrets are needed, where to get the certs,
and how macOS signing actually fires today.

## Signing matrix

| Platform | Format | Secrets required |
|---|---|---|
| macOS | Developer ID signing + Apple notarization | `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_API_ISSUER`, `APPLE_API_KEY`, `APPLE_API_KEY_PATH`, `APPLE_TEAM_ID` |
| Tauri auto-updater (future) | Ed25519 signature on the DMG | `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` |

All secrets are set in **Settings → Secrets and variables → Actions**
on the repo.

## macOS — Developer ID

### Prerequisites

- An Apple Developer Program membership ($99/year individual, $299/year org)
- macOS Keychain access on a trusted dev box

### One-time setup

1. **Create a "Developer ID Application" certificate** in
   [Apple Developer → Certificates](https://developer.apple.com/account/resources/certificates/list).
   Follow Apple's CSR-from-Keychain flow. Download the `.cer` and
   double-click to install in your login Keychain.

2. **Export the certificate as a `.p12`:**
   ```sh
   # In Keychain Access, find "Developer ID Application: Your Name (TEAM_ID)"
   # Right-click → Export → file format: .p12 → set a password
   ```

3. **Base64-encode the `.p12`** for secret storage:
   ```sh
   base64 -i DeveloperID.p12 | pbcopy
   ```

4. **Create an App Store Connect API key** at
   [App Store Connect → Integrations → Keys](https://appstoreconnect.apple.com/access/integrations/api).
   - Click **+** to generate a new key, label it "ORBIS notarization",
     grant **Developer** access.
   - Download the `.p8` file — **only available once**, store it safely.
   - Note the **Key ID** (10-char alphanumeric) and **Issuer ID**
     (UUID shown at the top of the page).

   We use API keys rather than an app-specific password so the secret
   doesn't have to be rotated every time an Apple ID password
   changes, and CI runs aren't subject to the 2FA prompts that
   password auth occasionally surfaces.

5. **Base64-encode the `.p8`** for secret storage:
   ```sh
   base64 -i AuthKey_<KEY_ID>.p8 | pbcopy
   ```

6. **Find your Team ID** at
   [Apple Developer → Membership](https://developer.apple.com/account#MembershipDetailsCard).
   It's also embedded in the `APPLE_SIGNING_IDENTITY` string
   ("Developer ID Application: Your Name (TEAMID)").

### Secrets to set

| Secret | Value |
|---|---|
| `APPLE_CERTIFICATE` | base64-encoded `.p12` from step 3 |
| `APPLE_CERTIFICATE_PASSWORD` | the `.p12` export password from step 2 |
| `APPLE_SIGNING_IDENTITY` | the full identity string, e.g. `Developer ID Application: Your Name (ABC123DEF4)` |
| `APPLE_API_ISSUER` | Issuer ID (UUID) from step 4 |
| `APPLE_API_KEY` | Key ID (10-char) from step 4 |
| `APPLE_API_KEY_PATH` | base64-encoded `.p8` from step 5 (CI decodes it to a temp file path before invoking Tauri) |
| `APPLE_TEAM_ID` | 10-char Team ID from step 6 |

Next tagged release will sign + notarize the `.dmg` and `.app`
inside. On semver tag builds, CI verifies the signed `.app`, checks
the embedded entitlements, runs Gatekeeper assessment, and validates
the stapled notarization tickets on both the build-tree `.app` and the
`ORBIS.app` mounted from the `.dmg`, plus the `.dmg` container itself.
Users no longer get the Gatekeeper warning on first open.

## Tauri auto-updater signing

The updater plugin (future PR) verifies each update's `.sig` against a
public key baked into `tauri.conf.json` at build time. Generate once,
keep the private key secret, paste the public key into the config.

```sh
cargo tauri signer generate -w ~/.tauri/orbis-updater.key
# prompts for a password; save it to a password manager

# Public key is printed to stdout + written to .pub
cat ~/.tauri/orbis-updater.key.pub
```

### Secrets to set

| Secret | Value |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | contents of `~/.tauri/orbis-updater.key` |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | password used at generation |

In `src-tauri/tauri.conf.json` (when the updater lands):
```json
{
  "plugins": {
    "updater": {
      "pubkey": "<contents of .pub file>",
      "endpoints": ["https://github.com/protoLabsAI/ORBIS/releases/latest/download/latest.json"]
    }
  }
}
```

### Current ORBIS updater public key

This is the public half of the keypair whose private half lives as
`TAURI_SIGNING_PRIVATE_KEY` in Infisical's `prod` env (synced to
GitHub Actions secrets). Paste verbatim into the `pubkey` field
above when the updater plugin gets wired. If the private key is
ever rotated, regenerate, update Infisical, and replace this block.

```
dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDhDQ0E3RjE2QzhFQTc0NEEKUldSS2RPcklGbi9LakpLM1g4YmE2eXd1ZE01RVNUaHh5bkk2VzZuWlE4ajdLTE9ENGRuNkZoWTYK
```

## Verifying a release is signed

### macOS
```sh
# Signature + notarization status
codesign --verify --deep --strict --verbose=2 "ORBIS.app"
codesign -dv "ORBIS.app" 2>&1 | grep "Authority=Developer ID Application:"
codesign -d --entitlements :- "ORBIS.app"
spctl --assess --type execute --verbose=4 "ORBIS.app"
xcrun stapler validate "ORBIS.app"

# DMG release artifact
spctl --assess --type open --context context:primary-signature --verbose=4 "ORBIS.dmg"
xcrun stapler validate "ORBIS.dmg"
```

Or run the project wrapper against a downloaded/built DMG:

```sh
scripts/validate-macos-native-audio.sh --release --dmg "ORBIS.dmg"
```

The wrapper requires the DMG path. If the local build `.app` is not present,
it mounts the DMG and validates the contained `ORBIS.app` directly. It checks
the app metadata, verifies the main executable is `arm64`, confirms the
bundled PyApp sidecar is present, verifies the signed entitlement set stays
narrow (microphone + network, no camera or broad code-signing exceptions),
runs the signing/notarization checks above, proves the DMG contains
`ORBIS.app` with the arm64 executable, sidecar, and first-run config
resources, and writes `macos-native-audio-validation.txt`.

### Tauri updater signatures
The updater plugin verifies each downloaded bundle against a sibling
`.sig` file using the pubkey baked into `tauri.conf.json`. Artifact
naming is platform-specific:

- **macOS** — `*.app.tar.gz` + `*.app.tar.gz.sig`. The Tauri v2
  updater does **not** use the DMG for macOS updates; you need
  `bundle.createUpdaterArtifacts: true` in `tauri.conf.json` for the
  tarball + sig to land in `target/release/bundle/macos/`.

The updater artifact is uploaded to the GitHub release alongside the
DMG by `desktop-build.yml` once updater artifacts are enabled.

## Rollback / key rotation

- Lost macOS cert → revoke at
  [Apple Developer → Certificates](https://developer.apple.com/account/resources/certificates/list),
  reissue, re-provision the secrets. Past releases stay signed with
  the old cert until it expires.
- Lost Tauri updater key → generate a new one + flip the pubkey in
  `tauri.conf.json`. This breaks auto-update for every installed
  version — users have to re-download manually. Don't lose it.

## Cost summary (yearly)

| | Individual | Org |
|---|---|---|
| Apple Developer Program | $99 | $299 |
| GitHub Actions macOS-14 runners | included in free tier for public, paid for private | same |

Total ~$99–$299/year plus GitHub Actions usage for signed macOS
releases.
