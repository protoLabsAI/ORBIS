"""voice.keycheck — WARN when an openai audio backend will silently 401."""

from __future__ import annotations

import logging

import pytest

from voice.keycheck import warn_if_placeholder_key


@pytest.mark.parametrize("key", ["", "not-needed", None, "  "])
def test_warns_on_placeholder_key_against_openai(caplog, key):
    with caplog.at_level(logging.WARNING):
        warn_if_placeholder_key("stt", "https://api.openai.com/v1", key)
    assert any("will 401" in r.message for r in caplog.records), key


def test_no_warn_with_a_real_key(caplog):
    with caplog.at_level(logging.WARNING):
        warn_if_placeholder_key("tts", "https://api.openai.com/v1", "sk-real-key")
    assert not caplog.records


def test_no_warn_for_local_keyless_endpoint(caplog):
    # A local server legitimately needs no key — placeholder is fine there,
    # so don't cry wolf.
    with caplog.at_level(logging.WARNING):
        warn_if_placeholder_key("stt", "http://127.0.0.1:8080/v1", "not-needed")
        warn_if_placeholder_key("tts", "http://localhost:1234/v1", "")
    assert not caplog.records


def test_message_names_the_backend_kind(caplog):
    with caplog.at_level(logging.WARNING):
        warn_if_placeholder_key("tts", "https://api.openai.com/v1", "")
    assert any(r.message.startswith("[tts]") for r in caplog.records)
