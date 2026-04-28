"""Tests for agent/prosody.py — prosody tag stripping."""

from __future__ import annotations

from agent.prosody import strip_tags


def test_strip_bracket_tag():
    assert strip_tags("[softly] hello") == "hello"


def test_strip_ssml_break():
    # No space inserted around removed tag — caller handles spacing.
    assert strip_tags('say<break time="300ms"/>this') == "saythis"


def test_strip_multiple_tags():
    assert strip_tags("[softly] hello [pause:300] world") == "hello world"


def test_strip_passthrough():
    assert strip_tags("plain text") == "plain text"


def test_strip_empty():
    assert strip_tags("") == ""
