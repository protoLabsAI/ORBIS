"""Tests for the on_summary_applied discriminator (R5).

Before: the handler walked context.messages looking for a system message
whose content differed from skill.system_prompt. Pipecat's summarizer
actually inserts the summary as a user-role message at index 1, so the
discriminator never matched the summary. It either matched the assembled
_effective_prompt (wrong content saved as the summary — silent data
corruption) or didn't match anything.

After: pipecat's summary_message_template is configured to wrap the
generated text in <orbis-summary>…</orbis-summary> tags. The handler
finds the tagged message anywhere in context.messages and extracts just
the inner text.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app import (
    SUMMARY_TAG_CLOSE,
    SUMMARY_TAG_OPEN,
    _extract_summary_text,
)


# --- _extract_summary_text -----------------------------------------------


def test_extracts_summary_from_tagged_user_message() -> None:
    """Pipecat injects the summary as a user role at index 1."""
    messages = [
        {"role": "system", "content": "persona prompt + tool blocks + recall"},
        {"role": "user", "content": f"{SUMMARY_TAG_OPEN}we talked about Paris{SUMMARY_TAG_CLOSE}"},
        {"role": "user", "content": "what's the weather"},
        {"role": "assistant", "content": "checking the weather"},
    ]
    assert _extract_summary_text(messages) == "we talked about Paris"


def test_extracts_summary_when_tags_have_whitespace() -> None:
    messages = [
        {"role": "user", "content": f"{SUMMARY_TAG_OPEN}\n  trip planning\n  next steps  \n{SUMMARY_TAG_CLOSE}"},
    ]
    assert _extract_summary_text(messages) == "trip planning\n  next steps"


def test_returns_none_when_no_tagged_message() -> None:
    """No summary in context yet — handler should NOT persist anything.

    This is the R5 regression case: the old code would have walked
    looking for a non-persona system message, found the assembled prompt
    (which differs from the raw persona), and saved THAT as the
    'summary'. The new code returns None, the handler skips persistence.
    """
    messages = [
        {"role": "system", "content": "persona prompt + tool blocks + recall"},
        {"role": "user", "content": "what's the weather"},
        {"role": "assistant", "content": "checking the weather"},
    ]
    assert _extract_summary_text(messages) is None


def test_returns_none_when_messages_empty() -> None:
    assert _extract_summary_text([]) is None


def test_returns_none_when_only_open_tag() -> None:
    """Malformed wrapping — not a valid tagged summary."""
    messages = [
        {"role": "user", "content": f"{SUMMARY_TAG_OPEN}half-written..."},
    ]
    assert _extract_summary_text(messages) is None


def test_returns_none_when_only_close_tag() -> None:
    messages = [
        {"role": "user", "content": f"...trailing{SUMMARY_TAG_CLOSE}"},
    ]
    assert _extract_summary_text(messages) is None


def test_returns_none_when_tags_in_wrong_order() -> None:
    """Close tag appears before open tag — not a valid wrapping."""
    messages = [
        {"role": "user", "content": f"prefix {SUMMARY_TAG_CLOSE} body {SUMMARY_TAG_OPEN} suffix"},
    ]
    assert _extract_summary_text(messages) is None


def test_returns_none_when_inner_is_empty() -> None:
    messages = [
        {"role": "user", "content": f"{SUMMARY_TAG_OPEN}{SUMMARY_TAG_CLOSE}"},
    ]
    assert _extract_summary_text(messages) is None


def test_returns_none_when_inner_is_whitespace_only() -> None:
    messages = [
        {"role": "user", "content": f"{SUMMARY_TAG_OPEN}   \n\t  {SUMMARY_TAG_CLOSE}"},
    ]
    assert _extract_summary_text(messages) is None


def test_first_tagged_message_wins() -> None:
    """If pipecat ever produced two tagged messages (shouldn't happen,
    but be deterministic), take the first."""
    messages = [
        {"role": "user", "content": f"{SUMMARY_TAG_OPEN}older summary{SUMMARY_TAG_CLOSE}"},
        {"role": "user", "content": f"{SUMMARY_TAG_OPEN}newer summary{SUMMARY_TAG_CLOSE}"},
    ]
    assert _extract_summary_text(messages) == "older summary"


def test_works_for_any_role_not_just_user() -> None:
    """Pipecat currently inserts as user, but if a future version moves
    it to system, the discriminator still works."""
    messages = [
        {"role": "system", "content": "persona prompt"},
        {"role": "system", "content": f"{SUMMARY_TAG_OPEN}we discussed it{SUMMARY_TAG_CLOSE}"},
    ]
    assert _extract_summary_text(messages) == "we discussed it"


def test_ignores_non_string_content() -> None:
    """Some pipecat message types can have list/None content; don't crash."""
    messages = [
        {"role": "user", "content": None},
        {"role": "user", "content": ["multipart", "content"]},
        {"role": "user", "content": f"{SUMMARY_TAG_OPEN}real summary{SUMMARY_TAG_CLOSE}"},
    ]
    assert _extract_summary_text(messages) == "real summary"


def test_ignores_messages_without_get_method() -> None:
    """Pipecat's LLMSpecificMessage doesn't behave like a dict — skip."""
    class _Opaque:
        pass
    messages = [
        _Opaque(),
        {"role": "user", "content": f"{SUMMARY_TAG_OPEN}safe summary{SUMMARY_TAG_CLOSE}"},
    ]
    assert _extract_summary_text(messages) == "safe summary"


# --- end-to-end with save_summary ----------------------------------------


def test_handler_saves_extracted_summary(tmp_path: Path, monkeypatch) -> None:
    """Mimic the on_summary_applied handler body: extract → save → load.

    Ensures we save just the inner text, not the wrapped form, so
    _recall_block reads the user-readable summary back.
    """
    monkeypatch.setenv("SESSION_STORE_DIR", str(tmp_path))
    # Re-import session_store with the patched env so its module-level
    # _DEFAULT_DIR picks up tmp_path.
    import importlib
    import agent.session_store as ss
    importlib.reload(ss)

    raw_summary = "User mentioned planning a trip to Paris in May."
    messages = [
        {"role": "system", "content": "long assembled persona prompt"},
        {"role": "user", "content": f"{SUMMARY_TAG_OPEN}{raw_summary}{SUMMARY_TAG_CLOSE}"},
        {"role": "user", "content": "anything else"},
    ]

    text = _extract_summary_text(messages)
    assert text == raw_summary
    ss.save_summary("default", text)

    # Round-trip: load_last_summary should return the raw summary, NOT
    # the tag-wrapped form, NOT the assembled persona prompt.
    loaded = ss.load_last_summary("default")
    assert loaded == raw_summary


def test_handler_skips_when_no_tagged_summary(tmp_path: Path, monkeypatch) -> None:
    """The R5 regression case: pre-fix, this would have saved the
    assembled persona prompt as the summary. Post-fix, no tagged
    message → no save."""
    monkeypatch.setenv("SESSION_STORE_DIR", str(tmp_path))
    import importlib
    import agent.session_store as ss
    importlib.reload(ss)

    messages = [
        {"role": "system", "content": "this is the assembled persona prompt + tool blocks"},
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "content": "12:34"},
    ]

    text = _extract_summary_text(messages)
    assert text is None
    # Don't call save_summary — the handler's branch when text is None
    # means we skip persistence and log a warning.
    loaded = ss.load_last_summary("default")
    assert loaded is None  # nothing was saved
