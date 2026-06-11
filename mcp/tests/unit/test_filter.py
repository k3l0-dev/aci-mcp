"""Unit tests for registry.filter.build_filter."""

import pytest
from registry.filter import build_filter


def test_empty_filters_returns_empty_string():
    assert build_filter("fvBD", {}) == ""


def test_single_filter():
    assert build_filter("fvBD", {"name": "servers"}) == 'eq(fvBD.name,"servers")'


def test_multiple_filters_wrapped_in_and():
    result = build_filter("fvBD", {"name": "servers", "arpFlood": "yes"})
    assert result.startswith("and(")
    assert 'eq(fvBD.name,"servers")' in result
    assert 'eq(fvBD.arpFlood,"yes")' in result


def test_class_name_used_as_prefix():
    result = build_filter("faultInst", {"code": "F0532"})
    assert result == 'eq(faultInst.code,"F0532")'


def test_class_with_digits_in_name():
    result = build_filter("l3extOut", {"name": "outside"})
    assert result == 'eq(l3extOut.name,"outside")'


def test_value_with_special_chars():
    result = build_filter("fvSubnet", {"ip": "10.0.0.1/24"})
    assert result == 'eq(fvSubnet.ip,"10.0.0.1/24")'


def test_rs_class_with_tn_prefix():
    result = build_filter("fvRsCons", {"tnVzBrCPName": "web-contract"})
    assert result == 'eq(fvRsCons.tnVzBrCPName,"web-contract")'


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({}, ""),
        ({"name": "x"}, 'eq(fvBD.name,"x")'),
    ],
)
def test_parametrized_basic(filters, expected):
    assert build_filter("fvBD", filters) == expected
