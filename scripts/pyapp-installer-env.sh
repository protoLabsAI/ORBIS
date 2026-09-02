#!/usr/bin/env bash
# Compile-time PyApp installer pins shared by release and local sidecar builds.
# PyApp bakes these values into the launcher; changing them after compilation
# does not affect an already-built binary.

ORBIS_PYAPP_VERSION="0.29.0"
export ORBIS_PYAPP_VERSION
export PYAPP_UV_ENABLED="1"
export PYAPP_UV_VERSION="0.12.9"
