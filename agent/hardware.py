"""Hardware acceleration probe for the bundled desktop flow.

ORBIS's desktop distribution targets Apple-Silicon Macs (MPS) and
PCs / Linux boxes with NVIDIA GPUs (CUDA via the cu128 torch wheel
pinned in the Dockerfile + packaging config). CPU Whisper is multi-
second per turn — not a voice-first experience — so the bundled app
refuses to start on unsupported hardware and points users at the
Docker self-host path instead.

The Docker CPU profile (``docker-compose.cpu.yml``) opts back into CPU
by setting ``ORBIS_ALLOW_CPU=1``; pytest runs on GPU-less CI boxes
do the same.

The probe is cheap (~100ms) — just a ``torch.cuda.is_available()`` /
``torch.backends.mps.is_available()`` check + one small tensor op to
confirm the detected device actually works end-to-end. Runs once at
startup, before Whisper / Kokoro load their models.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

logger = logging.getLogger(__name__)

Device = Literal["cuda", "mps", "cpu"]


class HardwareError(RuntimeError):
    """Raised when no supported accelerator is available and the caller
    has not explicitly opted into CPU via ``ORBIS_ALLOW_CPU=1``."""


_UNSUPPORTED_MSG = (
    "ORBIS requires an accelerated device — CUDA on NVIDIA (driver 570+) "
    "or Metal on Apple Silicon (M1 or newer). Neither was detected on this "
    "machine.\n\n"
    "If you have an NVIDIA GPU but CUDA is not being picked up, verify "
    "your driver version with `nvidia-smi` — ORBIS ships torch built "
    "against CUDA 12.8, which needs the 570-series driver or later.\n\n"
    "If you want to run ORBIS on CPU anyway (not recommended — voice turns "
    "will take several seconds each), set ORBIS_ALLOW_CPU=1 in the env.\n\n"
    "For a supported CPU-only path, use the Docker stack with the CPU "
    "override — see docker-compose.cpu.yml in the repo."
)


def detect_device() -> Device:
    """Probe the environment and return the device ORBIS should use.

    Returns:
        'cuda'  — NVIDIA GPU with a compatible driver
        'mps'   — Apple Silicon Metal backend
        'cpu'   — only when ``ORBIS_ALLOW_CPU=1`` is set

    Raises:
        HardwareError — when neither accelerator is available and the
            CPU opt-in flag is unset. The caller is expected to log + exit.
    """
    # Import torch lazily so ``from agent.hardware import …`` at the top of
    # app.py doesn't pay the torch-import cost before we've had a chance to
    # set HF_HOME / etc.
    import torch  # noqa: PLC0415

    if torch.cuda.is_available():
        _smoke_test(torch, "cuda")
        return "cuda"

    # MPS is Apple Silicon only. ``is_available()`` already covers the
    # "built with MPS + OS supports it" case; the matching ``is_built()``
    # check is redundant on official macOS arm64 wheels.
    if torch.backends.mps.is_available():
        _smoke_test(torch, "mps")
        return "mps"

    if os.environ.get("ORBIS_ALLOW_CPU") == "1":
        logger.warning(
            "[hardware] no accelerator detected; ORBIS_ALLOW_CPU=1 → using CPU "
            "(expect multi-second voice turns)"
        )
        return "cpu"

    raise HardwareError(_UNSUPPORTED_MSG)


def _smoke_test(torch_module, device: str) -> None:
    """Run a tiny matmul on the detected device. Surfaces driver /
    toolchain mismatches that ``is_available()`` can return True for
    (e.g. the cu130 wheel + driver 570 case we hit earlier) with a
    clear error rather than a cryptic shader compile crash ten seconds
    later during Whisper load."""
    try:
        x = torch_module.randn(8, 8, device=device)
        y = x @ x
        if device == "cuda":
            torch_module.cuda.synchronize()
        elif device == "mps":
            torch_module.mps.synchronize()
        _ = y.sum().item()
    except Exception as e:
        raise HardwareError(
            f"{device!r} reported available but a smoke-test matmul failed: "
            f"{e}. This usually means a driver / torch-wheel mismatch — "
            "check that your GPU driver supports the bundled CUDA / Metal "
            "version."
        ) from e
