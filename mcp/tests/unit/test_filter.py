"""Unit tests for registry.filter.build_filter."""

import pytest
from exceptions import FilterError
from registry.filter import build_filter


# ── Happy path ────────────────────────────────────────────────────────────────


def test_empty_filters_returns_empty_string():
    assert build_filter("fvBD", {}) == ""


def test_single_filter():
    assert build_filter("fvBD", {"name": "servers"}) == 'eq(fvBD.name,"servers")'


def test_multiple_filters_wrapped_in_and():
    result = build_filter("fvBD", {"name": "servers", "arpFlood": "yes"})
    assert result.startswith("and(")
    assert 'eq(fvBD.name,"servers")' in result
    assert 'eq(fvBD.arpFlood,"yes")' in result


def test_class_name_used_as_qualifier():
    assert build_filter("faultInst", {"code": "F0532"}) == 'eq(faultInst.code,"F0532")'


def test_cidr_value_passes_through():
    # Slash in IP/CIDR is valid — must not be rejected
    assert (
        build_filter("fvSubnet", {"ip": "10.0.0.1/24"})
        == 'eq(fvSubnet.ip,"10.0.0.1/24")'
    )


def test_tn_prefixed_attribute():
    assert (
        build_filter("fvRsCons", {"tnVzBrCPName": "web-contract"})
        == 'eq(fvRsCons.tnVzBrCPName,"web-contract")'
    )


# ── Value escaping ────────────────────────────────────────────────────────────


def test_value_with_double_quote_is_escaped():
    result = build_filter("fvBD", {"name": 'bd"test'})
    # The quote must be escaped — raw quote would break APIC filter syntax
    assert '\\"' in result
    assert result == 'eq(fvBD.name,"bd\\"test")'


def test_value_with_backslash_is_escaped():
    result = build_filter("fvBD", {"descr": "path\\value"})
    assert "\\\\" in result


def test_value_with_both_backslash_and_quote():
    result = build_filter("fvBD", {"descr": 'say\\"hello'})
    # backslash escaped first, then quote escaped
    assert result == 'eq(fvBD.descr,"say\\\\\\"hello")'


def test_value_with_spaces_passes_through():
    result = build_filter("fvTenant", {"descr": "my tenant"})
    assert result == 'eq(fvTenant.descr,"my tenant")'


def test_value_with_special_chars_other_than_quote():
    # Hyphens, underscores, dots in DN-like values must pass through
    result = build_filter("fvBD", {"dn": "uni/tn-OT/BD-servers"})
    assert "uni/tn-OT/BD-servers" in result


# ── Identifier validation ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "class_name",
    [
        "1BadClass",  # starts with digit
        "bad-class",  # hyphen not allowed
        "bad.class",  # dot not allowed
        "bad class",  # space not allowed
        "",  # empty
        "bad;class",  # semicolon
    ],
)
def test_invalid_class_name_raises_filter_error(class_name):
    with pytest.raises(FilterError):
        build_filter(class_name, {"name": "x"})


@pytest.mark.parametrize(
    "attr",
    [
        "1badAttr",  # starts with digit
        "bad-attr",  # hyphen
        "bad.attr",  # dot
        "",  # empty
    ],
)
def test_invalid_attribute_name_raises_filter_error(attr):
    with pytest.raises(FilterError):
        build_filter("fvBD", {attr: "val"})


@pytest.mark.parametrize(
    "class_name",
    [
        "fvBD",
        "l3extOut",
        "faultInst",
        "fvRsCons",
        "vzBrCP",
        "aaaUser",
    ],
)
def test_valid_aci_class_names_accepted(class_name):
    # Should not raise
    build_filter(class_name, {"name": "x"})


# ── Parametrized basics ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({}, ""),
        ({"name": "x"}, 'eq(fvBD.name,"x")'),
    ],
)
def test_parametrized_basic(filters, expected):
    assert build_filter("fvBD", filters) == expected
