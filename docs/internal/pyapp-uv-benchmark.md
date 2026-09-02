# PyApp UV installer benchmark (#489)

Measured 2026-09-02 on Apple Silicon (`arm64`, macOS 26.5.2) using the ORBIS
0.2.169 sdist with the release extras `parakeet,smart-turn`, PyApp 0.29.0's
standalone CPython 3.11.14 distribution, and UV 0.12.9.

## Result

| Installer | Cold cache | Warm cache | Environment logical bytes | Environment allocated bytes |
| --- | ---: | ---: | ---: | ---: |
| pip 25.2 | 100.33 s | 54.47 s | 1,888,619,308 | 2,038,620,160 |
| UV 0.12.9 | 34.07 s | 2.56 s | 1,499,109,549 | 1,607,417,856 |

UV was 2.9x faster cold and 21.3x faster when recreating the environment from
its warm shared cache. Both environments exposed 180 installed distributions
with an identical package/version inventory, and successfully imported `app`,
`acp`, `server`, and `agent.delegate_adapters`.

The per-directory allocated byte count above double-counts APFS shared blocks.
A second warm installation measured with filesystem free blocks before/after
used about 77 MiB physically for UV (`78,832 KiB`) versus about 1.93 GiB for
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
`PYAPP_UV_VERSION=0.12.9`. Their installer cores were benchmarked against fresh
copies of PyApp's exact standalone Python distribution, reproducing PyApp's
full-isolation pip and UV commands. Install roots and pip/UV caches were all
under a disposable `/tmp/orbis-pyapp-489.*` directory.

PyApp exposes `PYAPP_INSTALL_DIR_ORBIS` but no override for its own macOS cache
directory. Running the launchers from a test account would therefore acquire
locks and populate the active user's PyApp cache. The benchmark did not do
that: it left both the installed ORBIS app and user PyApp/UV caches untouched.
This means the timings exclude the identical PyApp distribution-unpack wrapper
around the measured installer commands.

Production UV package artifacts default to `~/.cache/uv`; PyApp separately
caches the pinned UV executable under `~/Library/Caches/pyapp/uv`. The benchmark
redirected the package cache to its temporary directory and only read an
existing PyApp distribution archive as the common Python input.

## Offline behavior

With network denied by the macOS sandbox, UV recreated a fresh environment
from the warm isolated cache in 6.23 seconds. With an empty cache it failed
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
