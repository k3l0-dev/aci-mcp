"""Unit tests for _coerce_json_str, _JsonList, _JsonDict in main.py.

LLMs sometimes JSON-encode list/dict tool arguments as strings instead of
sending native JSON arrays/objects (double-encoding).  These tests verify that
the BeforeValidator coerces the string transparently before Pydantic validates
the final type.
"""

import pytest
from pydantic import BaseModel, ValidationError

from main import _JsonDict, _JsonList, _coerce_json_str


# ── _coerce_json_str (raw function) ──────────────────────────────────────────


def test_coerce_list_string_to_list():
    assert _coerce_json_str('["a", "b"]') == ["a", "b"]


def test_coerce_dict_string_to_dict():
    assert _coerce_json_str('{"key": "val"}') == {"key": "val"}


def test_coerce_passthrough_for_list():
    v = ["a", "b"]
    assert _coerce_json_str(v) is v


def test_coerce_passthrough_for_dict():
    v = {"key": "val"}
    assert _coerce_json_str(v) is v


def test_coerce_passthrough_for_none():
    assert _coerce_json_str(None) is None


def test_coerce_invalid_json_string_returned_unchanged():
    # Not valid JSON — must be returned as-is so Pydantic raises the right error.
    assert _coerce_json_str("not-json") == "not-json"


def test_coerce_empty_string_returned_unchanged():
    assert _coerce_json_str("") == ""


# ── _JsonList via Pydantic model ──────────────────────────────────────────────


class _ListModel(BaseModel):
    items: _JsonList | None = None


def test_json_list_accepts_native_list():
    m = _ListModel(items=["x", "y"])
    assert m.items == ["x", "y"]


def test_json_list_coerces_json_encoded_string():
    m = _ListModel(items='["infraRsDomP", "infraRsVlanNs"]')
    assert m.items == ["infraRsDomP", "infraRsVlanNs"]


def test_json_list_accepts_none():
    m = _ListModel(items=None)
    assert m.items is None


def test_json_list_rejects_plain_string():
    with pytest.raises(ValidationError):
        _ListModel(items="not-a-list")


def test_json_list_rejects_json_string_of_non_list():
    # JSON-encoded dict is not a list
    with pytest.raises(ValidationError):
        _ListModel(items='{"key": "val"}')


# ── _JsonDict via Pydantic model ──────────────────────────────────────────────


class _DictModel(BaseModel):
    attrs: _JsonDict | None = None


def test_json_dict_accepts_native_dict():
    m = _DictModel(attrs={"name": "servers"})
    assert m.attrs == {"name": "servers"}


def test_json_dict_coerces_json_encoded_string():
    m = _DictModel(attrs='{"name": "servers"}')
    assert m.attrs == {"name": "servers"}


def test_json_dict_accepts_none():
    m = _DictModel(attrs=None)
    assert m.attrs is None


def test_json_dict_rejects_plain_string():
    with pytest.raises(ValidationError):
        _DictModel(attrs="not-a-dict")


def test_json_dict_rejects_json_string_of_non_dict():
    # JSON-encoded list is not a dict
    with pytest.raises(ValidationError):
        _DictModel(attrs='["a", "b"]')
