#!/usr/bin/env bash
# Compile-time PyApp installer pins shared by release and local sidecar builds.
# PyApp bakes these values into the launcher; changing them after compilation
# does not affect an already-built binary.

ORBIS_PYAPP_VERSION="0.29.0"
export ORBIS_PYAPP_VERSION
# GitHub's digest for ofek/pyapp v0.29.0 source.tar.gz. The build helper
# verifies this before applying our narrow UV-checksum patch.
ORBIS_PYAPP_SOURCE_SHA256="0533004baf6d1d46ef15abad02e98881ec92291c024182a36b57931c8d66df5f"
export ORBIS_PYAPP_SOURCE_SHA256
export PYAPP_UV_ENABLED="1"
export PYAPP_UV_VERSION="0.12.9"
# Official astral-sh/uv 0.12.9 release-asset checksums. Keep every digest in
# this block in lockstep with PYAPP_UV_VERSION; build-patched-pyapp.sh selects
# the checksum matching the Rust host that PyApp uses for its artifact name.
PYAPP_UV_SHA256_AARCH64_APPLE_DARWIN="301f72afaf54060f92da7016cb0115bd077f43a9c8e39c1d8170a0bac80fd398"
PYAPP_UV_SHA256_X86_64_UNKNOWN_LINUX_GNU="ec7a99cd05e0cd7f80243f135ce1361c76835cb0ee60055d14d20eba8eba1460"
PYAPP_UV_SHA256_X86_64_PC_WINDOWS_MSVC="ddbfcee1ac615a0499f6aa97b5ec8ebdf3ee4a7714a48055ec2ba0030e3cf810"
export PYAPP_UV_SHA256_AARCH64_APPLE_DARWIN
export PYAPP_UV_SHA256_X86_64_UNKNOWN_LINUX_GNU
export PYAPP_UV_SHA256_X86_64_PC_WINDOWS_MSVC
# Match pip's eager .pyc generation so the first real ORBIS launch does not
# inherit deferred compilation work from the faster installer.
export PYAPP_PIP_EXTRA_ARGS="--compile-bytecode"
