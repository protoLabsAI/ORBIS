"""Guards for the incomplete-turn re-prompt timeout hardening (orbis-3ss).

FILTER_INCOMPLETE_TURNS=1 suppresses replies until the model emits a ✓
marker; pipecat re-prompts after incomplete_long_timeout (default 10s) /
incomplete_short_timeout (default 5s). When the model mis-marks a complete
turn, those defaults are a 10-second silent hang. app.py must override them
with low values (env-tunable) whenever the filter is enabled.
"""

from __future__ import annotations

from pathlib import Path

from pipecat.processors.aggregators.llm_response_universal import UserTurnCompletionConfig

ROOT = Path(__file__).resolve().parents[1]


def test_pipecat_defaults_are_the_dangerous_ones() -> None:
    # If pipecat ever lowers these we can drop our override — this test
    # tells us when that day comes.
    d = UserTurnCompletionConfig()
    assert d.incomplete_long_timeout == 10.0
    assert d.incomplete_short_timeout == 5.0


def test_config_accepts_low_overrides() -> None:
    c = UserTurnCompletionConfig(incomplete_long_timeout=3.0, incomplete_short_timeout=2.0)
    assert c.incomplete_long_timeout == 3.0
    assert c.incomplete_short_timeout == 2.0


def test_app_overrides_reprompt_timeouts_when_filter_enabled() -> None:
    """Source guard: the filter block must build a UserTurnCompletionConfig
    with both re-prompt timeouts so it can never fall back to the 10s default."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    # Filter enable + config construction are wired together.
    assert 'filter_incomplete_user_turns"] = True' in src
    assert "user_turn_completion_config" in src
    assert "UserTurnCompletionConfig(" in src
    assert "incomplete_long_timeout" in src
    assert "incomplete_short_timeout" in src
    # Env-tunable, with low defaults (not pipecat's 10s/5s).
    assert 'INCOMPLETE_LONG_TIMEOUT", "3.0"' in src
    assert 'INCOMPLETE_SHORT_TIMEOUT", "2.0"' in src
