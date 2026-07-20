# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

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
        # contains — {"pkg:Class": ""} dict, projected to sorted flat names.
        "contains": {
            "fv:Subnet": "",
            "fv:RsCtx": "",
            "tag:Tag": "",
            "fault:Inst": "",
        },
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

# Rich fvSubnet fixture mirroring the real per-property jsonmeta structure
# (modelType, validValues with a "defaultValue" marker, isNaming, readOnly,
# mandatory, list-of-lines comment with the "null" sentinel).  Used to exercise
# property_details projection.
_FVSUBNET_SCHEMA = {
    "fvSubnet": {
        "identifiedBy": ["ip"],
        "rnFormat": "subnet-[{ip}]",
        "containedBy": {"fv:BD": ""},
        "label": "Subnet",
        "isAbstract": False,
        "isConfigurable": True,
        "className": "Subnet",
        "classPkg": "fv",
        "contains": {"fault:Inst": "", "tag:Tag": ""},
        "properties": {
            "ip": {
                "modelType": "address:Ip",
                "baseType": "address:Ip",
                "isConfigurable": True,
                "isNaming": True,
                "readWrite": False,
                "readOnly": False,
                "createOnly": False,
                "comment": ["The IP address and mask of the default gateway."],
            },
            "scope": {
                "modelType": "fv:RouteScp",
                "baseType": "scalar:Bitmask8",
                "isConfigurable": True,
                "readWrite": True,
                "readOnly": False,
                "createOnly": False,
                "default": "private",
                "validValues": [
                    {"value": "private", "localName": "defaultValue"},
                    {"value": "2", "localName": "private"},
                    {"value": "1", "localName": "public"},
                    {"value": "4", "localName": "shared"},
                ],
                "comment": ["The network visibility of the subnet."],
            },
            "preferred": {
                "modelType": "scalar:Bool",
                "isConfigurable": True,
                "readWrite": True,
                "default": "false",
                "validValues": [
                    {"value": "false", "localName": "defaultValue"},
                    {"value": "false", "localName": "no"},
                    {"value": "true", "localName": "yes"},
                ],
            },
            "descr": {
                "modelType": "naming:Descr",
                "baseType": "string:Basic",
                "isConfigurable": True,
                "readWrite": True,
                "comment": ["Specifies the description of a policy component."],
            },
            "operSt": {
                "modelType": "fv:OperStQual",
                "isConfigurable": False,
                "readOnly": True,
                "comment": ["Operational state of the subnet."],
            },
            "name": {
                "modelType": "naming:Name",
                "isConfigurable": True,
                "readWrite": True,
                "mandatory": True,
                "comment": ["null"],  # sentinel — must be dropped
            },
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
    (tmp_path / "fvSubnet.json").write_text(
        json.dumps(_FVSUBNET_SCHEMA), encoding="utf-8"
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


# ── contains projection (Task 1) ─────────────────────────────────────────────


def test_contains_projected_to_sorted_flat_names(schema_dir):
    # {"fv:Subnet","fv:RsCtx","tag:Tag","fault:Inst"} → sorted flat names
    schema = load_schema("fvBD", schema_dir)
    assert schema["contains"] == ["faultInst", "fvRsCtx", "fvSubnet", "tagTag"]


def test_contains_is_list_of_strings(schema_dir):
    schema = load_schema("fvBD", schema_dir)
    assert isinstance(schema["contains"], list)
    assert all(isinstance(c, str) for c in schema["contains"])
    # colon must be gone — names are ready to feed to query/get_schema
    assert all(":" not in c for c in schema["contains"])


def test_contains_absent_when_class_has_no_children(schema_dir):
    # nwItem fixture declares no "contains" key
    schema = load_schema("nwItem", schema_dir)
    assert "contains" not in schema


# ── property_details projection (Task 2) ──────────────────────────────────────


def test_property_details_absent_by_default(schema_dir):
    schema = load_schema("fvSubnet", schema_dir)
    assert "property_details" not in schema
    # the cheap name list is still present
    assert "properties" in schema


def test_property_details_full_dump_covers_all_properties(schema_dir):
    schema = load_schema("fvSubnet", schema_dir, include_property_details=True)
    assert set(schema["property_details"].keys()) == set(schema["properties"])


def test_properties_filter_limits_to_requested(schema_dir):
    schema = load_schema("fvSubnet", schema_dir, properties_filter=["scope", "ip"])
    assert set(schema["property_details"].keys()) == {"scope", "ip"}


def test_properties_filter_skips_unknown_names(schema_dir):
    schema = load_schema(
        "fvSubnet", schema_dir, properties_filter=["scope", "doesNotExist"]
    )
    assert set(schema["property_details"].keys()) == {"scope"}


def test_property_detail_enum_shape(schema_dir):
    schema = load_schema("fvSubnet", schema_dir, properties_filter=["scope"])
    scope = schema["property_details"]["scope"]
    assert scope["type"] == "fv:RouteScp"
    assert scope["access"] == "read-write"
    assert scope["default"] == "private"
    # localNames minus the "defaultValue" marker, order preserved
    assert scope["options"] == ["private", "public", "shared"]
    assert scope["comment"] == "The network visibility of the subnet."


def test_property_detail_naming_is_create_only(schema_dir):
    schema = load_schema("fvSubnet", schema_dir, properties_filter=["ip"])
    ip = schema["property_details"]["ip"]
    assert ip["naming"] is True
    # naming props carry no read/write flag — treated as immutable after create
    assert ip["access"] == "create-only"
    assert "options" not in ip


def test_property_detail_read_only_when_not_configurable(schema_dir):
    schema = load_schema("fvSubnet", schema_dir, properties_filter=["operSt"])
    assert schema["property_details"]["operSt"]["access"] == "read-only"


def test_property_detail_mandatory_flag_and_null_comment_dropped(schema_dir):
    schema = load_schema("fvSubnet", schema_dir, properties_filter=["name"])
    name = schema["property_details"]["name"]
    assert name["mandatory"] is True
    # the "null" sentinel comment must not surface
    assert "comment" not in name


def test_property_detail_omits_absent_fields(schema_dir):
    # descr has no enum, no default, no naming/mandatory flags
    schema = load_schema("fvSubnet", schema_dir, properties_filter=["descr"])
    descr = schema["property_details"]["descr"]
    assert descr["access"] == "read-write"
    assert "options" not in descr
    assert "default" not in descr
    assert "naming" not in descr
    assert "mandatory" not in descr
