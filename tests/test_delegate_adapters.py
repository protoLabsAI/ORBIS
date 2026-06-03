"""Tests for the delegate-adapter registry — the extension point for delegate
types. Covers the registry, capability flags, config schema / the
``/api/delegate-types`` contract shape, and that parse/validate route through
the adapters identically to the pre-refactor behavior.
"""

from __future__ import annotations

import pytest

from agent.delegate_adapters import (
    DelegateValidationError,
    all_adapter_types,
    delegate_type_specs,
    get_adapter,
)
from agent.delegates import _parse_entry


# --- registry ---------------------------------------------------------------


def test_builtin_types_registered():
    assert set(all_adapter_types()) == {"a2a", "openai", "acp"}


def test_get_adapter_case_insensitive():
    assert get_adapter("A2A").type == "a2a"
    assert get_adapter("OpenAI").type == "openai"


def test_get_adapter_unknown_raises():
    with pytest.raises(KeyError):
        get_adapter("grpc")


# --- capabilities -----------------------------------------------------------


def test_session_types_stream_and_inject():
    for t in ("a2a", "acp"):
        caps = get_adapter(t).capabilities
        assert caps.stream and caps.inject and caps.session and not caps.oneshot


def test_openai_is_oneshot():
    caps = get_adapter("openai").capabilities
    assert caps.oneshot and not caps.stream and not caps.inject and not caps.session


def test_oneshot_inject_unsupported():
    import asyncio

    from agent.delegates import Delegate, DelegateError

    d = Delegate(name="o", description="d", type="openai", url="http://x/v1", model="m")
    with pytest.raises(DelegateError):
        asyncio.run(get_adapter("openai").inject(d, "operator note"))


# --- config schema / contract ----------------------------------------------


def test_config_schema_required_fields():
    a2a_keys = {f.key: f for f in get_adapter("a2a").config_schema()}
    assert a2a_keys["url"].required
    openai_keys = {f.key: f for f in get_adapter("openai").config_schema()}
    assert openai_keys["url"].required and openai_keys["model"].required
    acp_keys = {f.key: f for f in get_adapter("acp").config_schema()}
    assert acp_keys["command"].required and acp_keys["workdir"].required


def test_delegate_type_specs_shape():
    specs = {s["type"]: s for s in delegate_type_specs()}
    assert set(specs) == {"a2a", "openai", "acp"}
    for s in specs.values():
        assert set(s["capabilities"]) == {"stream", "inject", "session", "oneshot"}
        assert isinstance(s["fields"], list) and s["fields"]
        for f in s["fields"]:
            assert {"key", "label", "kind", "required"} <= set(f)


# --- parse routes through adapters (behavior preserved) ---------------------


def test_parse_a2a_resolves_auth(monkeypatch):
    monkeypatch.setenv("AVA_KEY", "secret")
    d = _parse_entry({
        "name": "ava", "type": "a2a", "description": "hi",
        "url": "http://ava:3008/a2a",
        "auth": {"scheme": "apiKey", "credentialsEnv": "AVA_KEY"},
    })
    assert d is not None and d.type == "a2a"
    assert d.auth_headers()["X-API-Key"] == "secret"


def test_parse_unknown_type_skipped():
    assert _parse_entry({
        "name": "x", "type": "grpc", "description": "d", "url": "http://x",
    }) is None


# --- validate routes through adapters ---------------------------------------


def test_validate_unknown_type_message():
    from agent.delegate_config_store import validate_entry

    with pytest.raises(DelegateValidationError) as ei:
        validate_entry({"name": "x", "type": "grpc", "description": "d"})
    # message enumerates the registered types
    assert "a2a" in str(ei.value) and "grpc" in str(ei.value)


def test_validate_acp_requires_workdir():
    from agent.delegate_config_store import validate_entry

    with pytest.raises(DelegateValidationError) as ei:
        validate_entry({
            "name": "p", "type": "acp", "description": "d", "command": "proto",
        })
    assert "workdir" in str(ei.value)
