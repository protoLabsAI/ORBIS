# PyApp UV installer benchmark (#489)

Measured 2026-09-02 on Apple Silicon (`arm64`, macOS 26.5.2) using the ORBIS
0.2.169 sdist with the release extras `parakeet,smart-turn`, PyApp 0.29.0's
standalone CPython 3.11.14 distribution, and UV 0.12.9.

## Result

| Installer | Cold cache | Warm cache | Environment logical bytes | Environment allocated bytes |
| --- | ---: | ---: | ---: | ---: |
| pip 25.2 | 100.33 s | 54.47 s | 1,888,619,308 | 2,038,620,160 |
| UV 0.12.9 + bytecode | 46.34 s | 9.78 s | 1,882,361,821 | 2,044,239,872 |

UV was 2.2x faster cold and 5.6x faster when recreating the environment from
its warm shared cache. UV was passed its officially supported
`--compile-bytecode` install argument, matching pip's eager bytecode generation
and removing deferred first-launch work from the comparison. Both environments
exposed 180 installed distributions with an identical package/version
inventory, and successfully imported `app`, `acp`, `server`, and
`agent.delegate_adapters`.

The per-directory allocated byte count above double-counts APFS shared blocks.
A second warm installation measured with filesystem free blocks before/after
used about 402 MiB physically for UV (`411,600 KiB`) versus about 1.93 GiB for
pip (`2,028,648 KiB`). This is the updater-relevant number: each ORBIS version
still gets an isolated PyApp environment, but UV reuses cached artifacts with
APFS copy-on-write cloning instead of copying the dependency tree again.

Installer cache sizes after the cold run were 428,884,063 logical bytes for pip
and 1,492,156,862 for UV. UV deliberately keeps extracted artifacts in its
shared cache; the one-time cache is larger, but it avoids the repeated network
and physical disk cost on each app update.

## Method and safety boundary

Two official PyApp 0.29.0 sidecars were compiled from the same embedded sdist,
one with the legacy pip path and one with `PYAPP_UV_ENABLED=1` plus
`PYAPP_UV_VERSION=0.12.9`. The release configuration also passes
`--compile-bytecode` to UV. Their installer cores were benchmarked against
fresh copies of PyApp's exact standalone Python distribution, reproducing
PyApp's full-isolation pip and UV commands. Install roots and pip/UV caches
were all under a disposable `/tmp/orbis-pyapp-489.*` directory.

PyApp exposes `PYAPP_INSTALL_DIR_ORBIS` but no override for its own macOS cache
directory. Running the launchers from a test account would therefore acquire
locks and populate the active user's PyApp cache. The benchmark did not do
that: it left both the installed ORBIS app and user PyApp/UV caches untouched.
This means the install timings exclude the identical PyApp
distribution-unpack wrapper around the measured installer commands. As a
separate end-to-end check, the compiled UV environment booted the real ORBIS
entry point and emitted `ORBIS_READY` after 14.55 seconds with database, model,
session, and OAuth paths redirected into the disposable root. After that boot,
the pip and UV roots remained comparable in size: 1,890,212,403 versus
1,884,619,337 logical bytes, and 2,040,377,344 versus 2,045,394,944 allocated
bytes. The pip environment had been imported before the timed launch, so its
9.53-second ready time is recorded below but is not claimed as a fair launch
speed comparison.

Production UV package artifacts default to `~/.cache/uv`; PyApp separately
caches the pinned UV executable under `~/Library/Caches/pyapp/uv`. The benchmark
redirected the package cache to its temporary directory and only read an
existing PyApp distribution archive as the common Python input.

## Offline behavior

With network denied by the macOS sandbox, UV recreated a fresh environment
from the warm isolated cache in 9.47 seconds. With an empty cache it failed
closed after 4.1 seconds while trying to reach PyPI. Enabling UV therefore does
not make a fresh-machine installation offline: it makes later environment
recreation cache-backed. The embedded ORBIS sdist alone cannot supply its 178
third-party packages.

## Reproduction outline

1. Build the web bundle and `orbis-0.2.169.tar.gz`.
2. Compile PyApp 0.29.0 twice with identical ORBIS settings, toggling only
   `PYAPP_UV_ENABLED` and pinning UV to 0.12.9 for the UV binary.
3. Extract PyApp's CPython 3.11.14 distribution into a fresh temporary root for
   every run.
4. Install `orbis-0.2.169.tar.gz[parakeet,smart-turn]` with isolated pip or UV
   system-Python mode, using a separate temporary cache per installer.
5. Repeat into another fresh root with that installer's cache warm. Sum file
   logical sizes and allocated blocks; measure a further warm install using
   filesystem free blocks to capture APFS clone sharing.
6. Deny network with `sandbox-exec`; verify warm-cache success and empty-cache
   failure.

## Raw result summary

The disposable run logs and roots were retained through review under
`/tmp/orbis-pyapp-489.sMaJ23`. The values transcribed from them are:

```text
pip cold:                 real 100.33  user 38.91  sys 14.71
pip warm:                 real  54.47  user 36.30  sys 12.53
uv bytecode cold:         real  46.34  user 31.36  sys 20.26
uv bytecode warm:         real   9.78  user 26.90  sys 12.39
uv bytecode warm offline: real   9.47  user 27.12  sys 12.74

pip post-install: logical 1,888,619,308; allocated 2,038,620,160
uv post-install:  logical 1,882,361,821; allocated 2,044,239,872
pip post-ready:   logical 1,890,212,403; allocated 2,040,377,344
uv post-ready:    logical 1,884,619,337; allocated 2,045,394,944

warm physical allocation: pip 2,028,648 KiB; uv 411,600 KiB
installed inventory:      identical, 180 distributions
uv installed entry point: ORBIS_READY in 14.55 s (15.08 s process wall time)
compiled UV sidecar SHA:  f951060a340a4438ac3f1893cebd8d49067408899fcb52ab1695d9d645fce4bb
```

## Bootstrap trust boundary

The release pins PyApp 0.29.0 and UV 0.12.9. Upstream PyApp does not expose a
UV-checksum setting, so ORBIS builds its launcher from PyApp's versioned source
release after verifying that source archive's pinned SHA-256 and applying a
narrow checked-in patch. The patched launcher embeds the official per-platform
UV archive SHA-256 and compares it after download but before archive extraction
or execution. A mismatched archive fails closed.

The pinned Apple Silicon digest is
`301f72afaf54060f92da7016cb0115bd077f43a9c8e39c1d8170a0bac80fd398`,
matching both the downloaded archive and Astral's published checksum for
[`uv-aarch64-apple-darwin.tar.gz` in the 0.12.9 release](https://github.com/astral-sh/uv/releases/tag/0.12.9).
The source/digest mapping and update procedure live in
[`scripts/pyapp-installer-env.sh`](../../scripts/pyapp-installer-env.sh) and
[`build-desktop-binary.md`](./build-desktop-binary.md).

This verifies release content, not the user's already-populated PyApp cache;
the launcher keeps PyApp's existing behavior of trusting a cached UV binary.
It also does not independently verify GitHub's artifact attestation at runtime.
