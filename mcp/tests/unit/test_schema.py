# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for registry.schema.load_schema.

All tests use synthetic schema files written to a temporary directory — no
dependency on the real data/schemas/ collection.  This means tests always run
and verify the actual parsing logic rather than just "does the file exist".
"""

import json

import pytest
from exceptions import SchemaLoadError
from registry.schema import load_schema

# ── Synthetic schema fixtures ─────────────────────────────────────────────────

# Realistic jsonmeta structure for fvBD — mirrors what the APIC actually returns.
_FVBD_SCHEMA = {
    "fvBD": {
        "identifiedBy": ["name"],
        "rnFormat": "BD-{name}",
        "containedBy": {"fv:Tenant": ""},  # dict format — normalised to list
        "label": "Bridge Domain",
        "isAbstract": False,
        "isConfigurable": True,
        "className": "BD",
        "classPkg": "fv",
        "dnFormats": ["uni/tn-{name}/BD-{name}"],
        "properties": {
            "name": {"type": "string"},
            "arpFlood": {"type": "scalar:Enum8"},
            "dn": {"type": "reference:BinRef"},
        },
        "relationTo": {
            "fvRsCtx": {"targetClass": "fvCtx", "cardinality": "One"},
            "fvRsBDToProfile": "fvProfile",  # plain string format
        },
        "relationFrom": {
            "fvRsBDSubnetToProfile": {"sourceClass": "fvSubnet"},
            "fvRsBDToNdP": "fvNdPolicy",  # plain string format
        },
    }
}

_ABSTRACT_SCHEMA = {
    "nwItem": {
        "identifiedBy": [],
        "rnFormat": "",
        "isAbstract": True,
        "isConfigurable": False,
        "className": "Item",
        "classPkg": "nw",
        "label": "Network Item",
    }
}


@pytest.fixture
def schema_dir(tmp_path):
    """Temporary directory with synthetic schema files."""
    (tmp_path / "fvBD.json").write_text(json.dumps(_FVBD_SCHEMA), encoding="utf-8")
    (tmp_path / "nwItem.json").write_text(
        json.dumps(_ABSTRACT_SCHEMA), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def versioned_schema_dir(tmp_path):
    """Schema files nested one level deep (versioned subdir layout)."""
    subdir = tmp_path / "mo-apic-6.0"
    subdir.mkdir()
    (subdir / "fvBD.json").write_text(json.dumps(_FVBD_SCHEMA), encoding="utf-8")
    return tmp_path


# ── Unknown class ─────────────────────────────────────────────────────────────


def test_unknown_class_returns_empty_dict(schema_dir):
    result = load_schema("nonExistentClassXYZ", schema_dir)
    assert result == {}


def test_unknown_class_does_not_raise(schema_dir):
    # Missing file is not an error — schema is optional for query planning
    load_schema("nonExistentClassXYZ", schema_dir)


# ── Scalar fields ─────────────────────────────────────────────────────────────


def test_known_class_returns_non_empty(schema_dir):
    assert load_schema("fvBD", schema_dir) != {}


def test_identified_by_extracted(schema_dir):
    schema = load_schema("fvBD", schema_dir)
    assert schema["identifiedBy"] == ["name"]


def test_rn_format_extracted(schema_dir):
    schema = load_schema("fvBD", schema_dir)
    assert schema["rnFormat"] == "BD-{name}"


def test_label_extracted(schema_dir):
    schema = load_schema("fvBD", schema_dir)
    assert schema["label"] == "Bridge Domain"


def test_class_name_and_pkg_extracted(schema_dir):
    schema = load_schema("fvBD", schema_dir)
    assert schema["className"] == "BD"
    assert schema["classPkg"] == "fv"


def test_abstract_flag_extracted(schema_dir):
    assert load_schema("fvBD", schema_dir)["isAbstract"] is False
    assert load_schema("nwItem", schema_dir)["isAbstract"] is True


# ── containedBy normalisation ─────────────────────────────────────────────────


def test_contained_by_dict_normalised_to_list(schema_dir):
    schema = load_schema("fvBD", schema_dir)
    cb = schema["containedBy"]
    assert isinstance(cb, list)
    assert "fv:Tenant" in cb


# ── properties ────────────────────────────────────────────────────────────────


def test_properties_is_sorted_list(schema_dir):
    schema = load_schema("fvBD", schema_dir)
    props = schema["properties"]
    assert isinstance(props, list)
    assert props == sorted(props)


def test_properties_contains_expected_keys(schema_dir):
    schema = load_schema("fvBD", schema_dir)
    for key in ("name", "arpFlood", "dn"):
        assert key in schema["properties"]


def test_properties_are_names_only_not_full_metadata(schema_dir):
    schema = load_schema("fvBD", schema_dir)
    # Each entry must be a string, not a dict with type metadata
    assert all(isinstance(p, str) for p in schema["properties"])


# ── relationTo normalisation ──────────────────────────────────────────────────


def test_relation_to_dict_format_normalised(schema_dir):
    schema = load_schema("fvBD", schema_dir)
    rel = schema["relationTo"]["fvRsCtx"]
    assert rel["targetClass"] == "fvCtx"
    assert rel["cardinality"] == "One"


def test_relation_to_plain_string_normalised(schema_dir):
    # "fvRsBDToProfile": "fvProfile" — plain string, cardinality defaults to ""
    schema = load_schema("fvBD", schema_dir)
    rel = schema["relationTo"]["fvRsBDToProfile"]
    assert rel["targetClass"] == "fvProfile"
    assert rel["cardinality"] == ""


# ── relationFrom normalisation ────────────────────────────────────────────────


def test_relation_from_dict_format_normalised(schema_dir):
    schema = load_schema("fvBD", schema_dir)
    rel = schema["relationFrom"]["fvRsBDSubnetToProfile"]
    assert rel["sourceClass"] == "fvSubnet"


def test_relation_from_plain_string_normalised(schema_dir):
    # "fvRsBDToNdP": "fvNdPolicy" — plain string
    schema = load_schema("fvBD", schema_dir)
    rel = schema["relationFrom"]["fvRsBDToNdP"]
    assert rel["sourceClass"] == "fvNdPolicy"


# ── Versioned subdir (glob fallback) ─────────────────────────────────────────


def test_schema_found_in_versioned_subdir(versioned_schema_dir):
    schema = load_schema("fvBD", versioned_schema_dir)
    assert schema != {}
    assert schema["label"] == "Bridge Domain"


# ── Error cases ───────────────────────────────────────────────────────────────


def test_malformed_json_raises_schema_load_error(tmp_path):
    (tmp_path / "fvBD.json").write_text("{not valid json}", encoding="utf-8")
    with pytest.raises(SchemaLoadError) as exc_info:
        load_schema("fvBD", tmp_path)
    assert exc_info.value.class_name == "fvBD"


def test_empty_json_object_raises_schema_load_error(tmp_path):
    (tmp_path / "fvBD.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SchemaLoadError) as exc_info:
        load_schema("fvBD", tmp_path)
    assert "empty" in str(exc_info.value)


def test_os_error_on_read_raises_schema_load_error(tmp_path):
    schema_file = tmp_path / "fvBD.json"
    schema_file.write_text(json.dumps(_FVBD_SCHEMA), encoding="utf-8")
    schema_file.chmod(0o000)  # remove read permission
    try:
        with pytest.raises(SchemaLoadError):
            load_schema("fvBD", tmp_path)
    finally:
        schema_file.chmod(0o644)
