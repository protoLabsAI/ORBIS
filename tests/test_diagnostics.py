"""Tests for the support-diagnostics bundle (#488).

``build_diagnostics_report()`` backs the in-app "Copy diagnostics" button. The
two things that MUST hold: it never leaks a provider secret, and it never
crashes just because one sub-system (metrics, config, persona) is unavailable —
it's the tool people reach for precisely *when* things are broken.
"""

from __future__ import annotations

import agent.config_store as config_store
import agent.metrics as metrics_mod
import app as app_module  # noqa: F401 — ensures routers are wired before build_diagnostics_report runs
from agent.config_store import REDACTED_SECRET
from server.routers.system import build_diagnostics_report


def test_report_has_versions_and_log_path():
    report = build_diagnostics_report()

    assert set(["app", "runtime", "metrics", "config", "logs"]) <= set(report)
    app = report["app"]
    assert app["version"]  # importlib version or the "unknown" fallback
    assert app["python"]
    assert app["platform"]
    assert app["machine"]
    # Log is NOT inlined (may carry transcripts) — only its path is surfaced.
    assert report["logs"]["path"].endswith("sidecar.log")


def test_report_redacts_config_secrets(monkeypatch):
    # A config carrying a live provider key must come back redacted — same
    # guarantee as GET /api/config, which the bundle embeds.
    monkeypatch.setattr(
        config_store,
        "read_config",
        lambda: {"llm": {"api_key": "sk-should-never-leak", "model": "gpt-4"}},
    )
    report = build_diagnostics_report()

    cfg = report["config"]
    assert cfg["llm"]["api_key"] == REDACTED_SECRET
    assert cfg["llm"]["model"] == "gpt-4"  # non-secret fields pass through
    # Belt-and-suspenders: the raw secret appears nowhere in the whole bundle.
    import json
    assert "sk-should-never-leak" not in json.dumps(report)


def test_report_survives_metrics_failure(monkeypatch):
    def _boom():
        raise RuntimeError("metrics subsystem down")

    monkeypatch.setattr(metrics_mod, "snapshot", _boom)
    report = build_diagnostics_report()

    # The metrics block degrades to an error string; the rest of the bundle
    # (the whole point — versions + config) is still there.
    assert "error" in report["metrics"]
    assert report["app"]["version"]
    assert "config" in report


def test_report_survives_config_failure(monkeypatch):
    def _boom():
        raise RuntimeError("orbis.yaml unreadable")

    monkeypatch.setattr(config_store, "read_config", _boom)
    report = build_diagnostics_report()

    assert "error" in report["config"]
    assert report["app"]["version"]  # still produced a usable bundle
