# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Unit tests for mcp/exceptions.py — hierarchy, messages, and attributes."""

import pytest
from exceptions import (
    AciMcpError,
    ApicAuthError,
    ApicConnectionError,
    ApicError,
    ApicResponseError,
    AuthenticationError,
    ConfigurationError,
    DescriptionsLoadError,
    FilterError,
    RegistryError,
    SchemaLoadError,
    UnknownClassError,
)


# ── Hierarchy ─────────────────────────────────────────────────────────────────


def test_all_exceptions_inherit_from_base():
    for cls in (
        ConfigurationError,
        AuthenticationError,
        RegistryError,
        DescriptionsLoadError,
        SchemaLoadError,
        UnknownClassError,
        FilterError,
        ApicError,
        ApicAuthError,
        ApicConnectionError,
        ApicResponseError,
    ):
        assert issubclass(cls, AciMcpError), f"{cls.__name__} must inherit AciMcpError"


def test_registry_subclasses():
    assert issubclass(DescriptionsLoadError, RegistryError)
    assert issubclass(SchemaLoadError, RegistryError)


def test_apic_subclasses():
    for cls in (ApicAuthError, ApicConnectionError, ApicResponseError):
        assert issubclass(cls, ApicError), f"{cls.__name__} must inherit ApicError"


def test_base_is_exception():
    assert issubclass(AciMcpError, Exception)


# ── ConfigurationError ────────────────────────────────────────────────────────


def test_configuration_error_message():
    exc = ConfigurationError("APIC_HOST is not set")
    assert "APIC_HOST" in str(exc)


def test_configuration_error_is_catchable_as_base():
    with pytest.raises(AciMcpError):
        raise ConfigurationError("missing env var")


# ── AuthenticationError ───────────────────────────────────────────────────────


def test_authentication_error_message():
    exc = AuthenticationError("missing or invalid API key")
    assert "API key" in str(exc)


def test_authentication_error_is_catchable_as_base():
    with pytest.raises(AciMcpError):
        raise AuthenticationError("bad token")


# ── DescriptionsLoadError ─────────────────────────────────────────────────────


def test_descriptions_load_error_message():
    exc = DescriptionsLoadError("file not found at /data/class-descriptions.json")
    assert "class-descriptions.json" in str(exc)


# ── SchemaLoadError ───────────────────────────────────────────────────────────


def test_schema_load_error_attributes():
    exc = SchemaLoadError("fvBD", "/data/schemas/fvBD.json", "unexpected end of JSON")
    assert exc.class_name == "fvBD"
    assert exc.path == "/data/schemas/fvBD.json"
    assert "fvBD" in str(exc)
    assert "unexpected end of JSON" in str(exc)


def test_schema_load_error_is_catchable_as_registry_error():
    with pytest.raises(RegistryError):
        raise SchemaLoadError("fvBD", "/path", "bad json")


# ── UnknownClassError ─────────────────────────────────────────────────────────


def test_unknown_class_error_with_suggestions():
    exc = UnknownClassError("fvBd", ["fvBD", "fvBDDef"], 15000)
    assert exc.class_name == "fvBd"
    assert "fvBD" in exc.suggestions
    assert exc.registry_size == 15000
    assert "fvBd" in str(exc)
    assert "fvBD" in str(exc)
    assert "15000" in str(exc)


def test_unknown_class_error_no_suggestions():
    exc = UnknownClassError("xyzNonExistent", [], 15000)
    assert exc.suggestions == []
    assert "No close matches" in str(exc)


def test_unknown_class_error_includes_action_hint():
    exc = UnknownClassError("fvBd", ["fvBD"], 100)
    assert "search_classes" in str(exc)


# ── FilterError ───────────────────────────────────────────────────────────────


def test_filter_error_message():
    exc = FilterError("Invalid class_name '123bad': must start with a letter")
    assert "123bad" in str(exc)


# ── ApicAuthError ─────────────────────────────────────────────────────────────


def test_apic_auth_error_attributes():
    exc = ApicAuthError("10.0.0.1", 401)
    assert exc.host == "10.0.0.1"
    assert exc.status == 401
    assert "10.0.0.1" in str(exc)
    assert "401" in str(exc)


def test_apic_auth_error_with_detail():
    exc = ApicAuthError("10.0.0.1", 403, "still unauthorized after re-authentication")
    assert "re-authentication" in str(exc)


def test_apic_auth_error_is_catchable_as_apic_error():
    with pytest.raises(ApicError):
        raise ApicAuthError("host", 401)


# ── ApicConnectionError ───────────────────────────────────────────────────────


def test_apic_connection_error_attributes():
    exc = ApicConnectionError("10.0.0.1", "request timed out after 30s")
    assert exc.host == "10.0.0.1"
    assert "10.0.0.1" in str(exc)
    assert "timed out" in str(exc)


def test_apic_connection_error_is_catchable_as_apic_error():
    with pytest.raises(ApicError):
        raise ApicConnectionError("host", "timeout")


# ── ApicResponseError ─────────────────────────────────────────────────────────


def test_apic_response_error_attributes():
    exc = ApicResponseError("https://apic/api/class/fvBD.json", "missing 'imdata' key")
    assert exc.url == "https://apic/api/class/fvBD.json"
    assert "imdata" in str(exc)
    assert "fvBD" in str(exc)


def test_apic_response_error_is_catchable_as_apic_error():
    with pytest.raises(ApicError):
        raise ApicResponseError("https://host/api", "bad json")
