"""The suite must not read the developer's personal runtime config.

`app.py` loads an optional runtime-tuning `.env` from the user-config dir
with `override=True` so the app can be retuned with a 5s restart instead
of an 80s rebuild. Under test that's poison: `override=True` beats
whatever a test arranged, so importing `app` pulled a real machine's
config into every test process and made the local suite disagree with CI.

Concretely: an `A2A_AUTH_TOKEN` in that file switched A2A auth on and
401'd `test_closed_loop_send_returns_answer`, which passes in CI. It read
as an order-dependent flake for a while. `conftest.py` sets
`ORBIS_SKIP_RUNTIME_ENV=1` before anything imports `app`; these tests pin
that contract at both ends.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

RUNTIME_ENV = Path.home() / "Library/Application Support/studio.protolabs.orbis/.env"


def test_conftest_sets_the_skip_flag_before_app_import() -> None:
    """If this is unset, every other test in the suite is reading whatever
    happens to be on the machine it runs on."""
    assert os.environ.get("ORBIS_SKIP_RUNTIME_ENV") == "1", (
        "conftest.py must set ORBIS_SKIP_RUNTIME_ENV before test modules "
        "import app — otherwise the user's runtime .env overrides test setup."
    )


def test_app_honors_the_skip_flag(tmp_path: Path) -> None:
    """The guard lives in app.py, so prove app.py respects it — with a
    real .env file on disk, in a subprocess (app is already imported in
    this one, so the module-level dotenv block has long since run)."""
    fake_home = tmp_path / "home"
    cfg = fake_home / "Library/Application Support/studio.protolabs.orbis"
    cfg.mkdir(parents=True)
    (cfg / ".env").write_text("ORBIS_HERMETICITY_CANARY=leaked\n")

    script = textwrap.dedent(
        """
        import os, sys
        sys.path.insert(0, %r)
        import app  # noqa: F401
        print(os.environ.get("ORBIS_HERMETICITY_CANARY", "<absent>"))
        """
        % str(Path(__file__).resolve().parent.parent)
    )

    def run(skip: bool) -> str:
        env = {**os.environ, "HOME": str(fake_home)}
        env.pop("ORBIS_HERMETICITY_CANARY", None)
        if skip:
            env["ORBIS_SKIP_RUNTIME_ENV"] = "1"
        else:
            env.pop("ORBIS_SKIP_RUNTIME_ENV", None)
        out = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=env, timeout=300,
        )
        assert out.returncode == 0, out.stderr[-2000:]
        return out.stdout.strip().splitlines()[-1]

    # Without the flag the runtime .env loads — that's the app's intended
    # behavior, and the thing that must not happen under test.
    assert run(skip=False) == "leaked"
    # With it, the file is ignored.
    assert run(skip=True) == "<absent>"


def test_real_runtime_env_is_not_leaking_into_this_process() -> None:
    """End-to-end: whatever is actually in the developer's runtime .env
    right now must not be visible here. No-op on CI (no such file), which
    is the point — CI is the baseline this keeps local runs honest against.
    """
    if not RUNTIME_ENV.is_file():
        return
    import app  # noqa: F401  — ensure the dotenv block has run

    keys = [
        line.split("=", 1)[0].strip()
        for line in RUNTIME_ENV.read_text().splitlines()
        if "=" in line and not line.strip().startswith("#")
    ]
    # A shell export legitimately wins over the file, so only flag keys
    # whose value actually came from the file.
    file_vals = {
        line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
        for line in RUNTIME_ENV.read_text().splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    leaked = [k for k in keys if os.environ.get(k) == file_vals[k] and file_vals[k]]
    assert not leaked, (
        f"the runtime .env leaked into the test process: {leaked}. "
        f"conftest.py's ORBIS_SKIP_RUNTIME_ENV guard is not working."
    )
