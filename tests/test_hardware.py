"""Tests for agent.hardware — device detection + hard-fail semantics."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def clear_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ORBIS_ALLOW_CPU", raising=False)


def _stub_torch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cuda_available: bool = False,
    mps_available: bool = False,
    smoke_raises: Exception | None = None,
):
    """Install a fake ``torch`` module with just the attributes
    agent.hardware touches."""
    cuda_ns = SimpleNamespace(
        is_available=lambda: cuda_available,
        synchronize=lambda: None,
    )
    mps_ns = SimpleNamespace(
        is_available=lambda: mps_available,
        synchronize=lambda: None,
    )
    backends_ns = SimpleNamespace(mps=mps_ns)

    def _randn(*_shape, device=None):
        if smoke_raises is not None and device in ("cuda", "mps"):
            raise smoke_raises

        class _Tensor:
            def __matmul__(self, _other): return self
            def sum(self): return self
            def item(self): return 0.0
        return _Tensor()

    torch_stub = SimpleNamespace(
        randn=_randn,
        cuda=cuda_ns,
        mps=mps_ns,
        backends=backends_ns,
    )
    monkeypatch.setitem(sys.modules, "torch", torch_stub)

    from agent import hardware
    importlib.reload(hardware)
    return hardware


# --- detect_device branches --------------------------------------------------


def test_detect_prefers_cuda(monkeypatch: pytest.MonkeyPatch):
    hw = _stub_torch(monkeypatch, cuda_available=True, mps_available=True)
    assert hw.detect_device() == "cuda"


def test_detect_falls_back_to_mps(monkeypatch: pytest.MonkeyPatch):
    hw = _stub_torch(monkeypatch, cuda_available=False, mps_available=True)
    assert hw.detect_device() == "mps"


def test_detect_raises_without_accel(monkeypatch: pytest.MonkeyPatch):
    hw = _stub_torch(monkeypatch, cuda_available=False, mps_available=False)
    with pytest.raises(hw.HardwareError) as exc:
        hw.detect_device()
    # Error message points users at the two valid paths.
    assert "CUDA" in str(exc.value)
    assert "Metal" in str(exc.value) or "MPS" in str(exc.value) or "Apple" in str(exc.value)
    assert "ORBIS_ALLOW_CPU" in str(exc.value)


def test_detect_allows_cpu_when_env_set(monkeypatch: pytest.MonkeyPatch):
    hw = _stub_torch(monkeypatch, cuda_available=False, mps_available=False)
    monkeypatch.setenv("ORBIS_ALLOW_CPU", "1")
    assert hw.detect_device() == "cpu"


# --- smoke-test failure maps to HardwareError ------------------------------


def test_smoke_failure_is_hardware_error(monkeypatch: pytest.MonkeyPatch):
    """``is_available()`` can lie when the torch wheel's CUDA version
    is newer than the installed driver — we hit this in the cu130 +
    driver-570 scenario earlier. The smoke-test surfaces it early with
    a useful message."""
    hw = _stub_torch(
        monkeypatch, cuda_available=True,
        smoke_raises=RuntimeError("device not ready"),
    )
    with pytest.raises(hw.HardwareError) as exc:
        hw.detect_device()
    assert "matmul failed" in str(exc.value)
    assert "driver" in str(exc.value).lower()


def test_allow_cpu_skips_smoke(monkeypatch: pytest.MonkeyPatch):
    """CPU opt-in path shouldn't run the accelerator smoke test."""
    hw = _stub_torch(
        monkeypatch, cuda_available=False, mps_available=False,
        smoke_raises=RuntimeError("should not run"),
    )
    monkeypatch.setenv("ORBIS_ALLOW_CPU", "1")
    assert hw.detect_device() == "cpu"
