"""Pytest bootstrap — runs before any test module imports `app`.

Sole job: keep the suite hermetic against the developer's own machine.

`app.py` loads an optional runtime-tuning `.env` from the user-config dir
(`~/Library/Application Support/studio.protolabs.orbis/.env`) with
`override=True`, so that Josh can retune VAD/echo-guard/model knobs with a
5s restart instead of an 80s rebuild. That's right for the app and wrong
for tests: `override=True` beats whatever a test set up, so importing
`app` pulled a real person's config into every test process.

The result was a suite that disagreed with CI. `A2A_AUTH_TOKEN` in that
file turned on A2A auth and 401'd `test_closed_loop_send_returns_answer` —
which passes in CI, and which looked for all the world like an
order-dependent flake. `tests/test_skill_llm_resolution.py` still carries a
fixture that hand-clears `LLM_MICRO_MODEL`/`LLM_ROUTER_MODEL` for the same
underlying reason; that's the symptom being treated one var at a time.

Root-level (not `tests/`) so it also covers `evals/` and anything added
later. Pytest imports rootdir conftest before collecting, which is what
makes this land ahead of the `app` import.
"""

from __future__ import annotations

import os

# Must happen at import time — a fixture would run too late, after test
# modules (and therefore `app`) are already imported.
os.environ["ORBIS_SKIP_RUNTIME_ENV"] = "1"
