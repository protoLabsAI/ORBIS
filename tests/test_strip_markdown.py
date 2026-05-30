"""Tests for _strip_markdown_for_speech (orbis-nhu).

Delegate replies come back as markdown; spoken verbatim by TTS that's
"asterisk asterisk number 4028". Flatten the unambiguous constructs while
leaving prose with stray * / _ alone.
"""

from __future__ import annotations

import pytest

from agent.tools import _strip_markdown_for_speech as strip


def test_observed_case() -> None:
    # The exact shape seen live: "Done. Filed **#4028** on the board: [M…](url)"
    out = strip("Done. Filed **#4028** on the protoMaker board: [M-1234](https://x/y).")
    assert "**" not in out
    assert "#4028" in out
    assert "M-1234" in out
    assert "http" not in out  # link URL dropped


@pytest.mark.parametrize("md,expected", [
    ("**bold**", "bold"),
    ("__bold__", "bold"),
    ("*italic*", "italic"),
    ("_italic_", "italic"),
    ("`code`", "code"),
    ("[label](http://u)", "label"),
    ("## Heading", "Heading"),
    ("> quoted", "quoted"),
])
def test_basic_constructs(md, expected) -> None:
    assert strip(md) == expected


def test_bullets_flattened() -> None:
    out = strip("- first\n- second")
    assert out == "first\nsecond"


def test_leaves_prose_punctuation_alone() -> None:
    # word-internal / arithmetic delimiters must not be treated as markdown
    assert strip("use snake_case here") == "use snake_case here"
    assert strip("the result is 2 * 3 = 6") == "the result is 2 * 3 = 6"


def test_empty_and_plain() -> None:
    assert strip("") == ""
    assert strip("just a normal sentence.") == "just a normal sentence."
