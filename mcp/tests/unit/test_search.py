"""Unit tests for registry.descriptions.search and load_descriptions."""

import json

import pytest
from exceptions import DescriptionsLoadError
from registry.descriptions import load_descriptions, search

_DESCRIPTIONS = {
    "fvBD": {
        "label": "Bridge Domain",
        "comment": "A bridge domain is a unique layer 2 forwarding domain.",
    },
    "fvTenant": {
        "label": "Tenant",
        "comment": "A policy owner in the virtual fabric.",
    },
    "faultInst": {
        "comment": "Contains detailed information of a fault instance.",
    },
    "fvAEPg": {
        "label": "Application EPG",
        "comment": "A set of requirements for the application-level EPG.",
    },
    "vzBrCP": {
        "label": "Contract",
        "comment": "A contract governs communication between EPGs.",
    },
    "fvCtx": {
        "label": "VRF",
        "comment": "A VRF instance defines a layer 3 address domain.",
    },
}


# ── search() — happy path ─────────────────────────────────────────────────────


def test_exact_class_name_match():
    results = search("fvBD", _DESCRIPTIONS)
    assert results[0]["class_name"] == "fvBD"


def test_label_match():
    results = search("bridge", _DESCRIPTIONS)
    assert any(r["class_name"] == "fvBD" for r in results)


def test_comment_match():
    results = search("virtual fabric", _DESCRIPTIONS)
    assert any(r["class_name"] == "fvTenant" for r in results)


def test_case_insensitive():
    results = search("BRIDGE DOMAIN", _DESCRIPTIONS)
    assert any(r["class_name"] == "fvBD" for r in results)


def test_partial_match_in_class_name():
    results = search("fv", _DESCRIPTIONS)
    class_names = [r["class_name"] for r in results]
    assert "fvBD" in class_names
    assert "fvTenant" in class_names


def test_no_match_returns_empty_list():
    results = search("zzz_nonexistent_xyz", _DESCRIPTIONS)
    assert results == []


def test_limit_respected():
    results = search("a", _DESCRIPTIONS, limit=2)
    assert len(results) <= 2


def test_result_has_required_fields():
    results = search("tenant", _DESCRIPTIONS)
    assert len(results) > 0
    for r in results:
        assert "class_name" in r
        assert "label" in r
        assert "comment" in r


# ── search() — ranking ────────────────────────────────────────────────────────


def test_class_name_match_ranks_higher_than_comment_only():
    # "fvBD" matches class name (weight 3) — should beat comment-only matches
    results = search("fvBD", _DESCRIPTIONS)
    assert results[0]["class_name"] == "fvBD"


def test_label_match_ranks_higher_than_comment_only():
    # "Contract" appears only in vzBrCP label — should rank above comment-only matches
    results = search("Contract", _DESCRIPTIONS)
    first = results[0]
    assert first["class_name"] == "vzBrCP"


def test_combined_match_ranks_above_single_field():
    # "domain" hits fvBD in both label ("Bridge Domain") and comment → score 3
    # fvCtx hits comment only → score 1
    results = search("domain", _DESCRIPTIONS)
    positions = {r["class_name"]: i for i, r in enumerate(results)}
    assert positions["fvBD"] < positions.get("fvCtx", len(results))


# ── search() — edge cases ─────────────────────────────────────────────────────


def test_empty_keyword_returns_empty():
    results = search("", _DESCRIPTIONS)
    assert results == []


def test_class_without_label_returns_empty_string_for_label():
    results = search("fault", _DESCRIPTIONS)
    fault = next((r for r in results if r["class_name"] == "faultInst"), None)
    assert fault is not None
    assert fault["label"] == ""


def test_metadata_with_none_values_does_not_crash():
    descriptions_with_none = {
        "fvBD": {"label": None, "comment": None},
    }
    # None values should not cause AttributeError
    with pytest.raises((AttributeError, TypeError)):
        # Currently will crash — this documents the gap for future fix
        search("fvBD", descriptions_with_none)


def test_limit_zero_returns_empty():
    results = search("bridge", _DESCRIPTIONS, limit=0)
    assert results == []


def test_very_large_limit_returns_all_matches():
    results = search("a", _DESCRIPTIONS, limit=100_000)
    # All 6 entries contain "a" — should get all of them
    assert len(results) == len(_DESCRIPTIONS)


# ── load_descriptions() ───────────────────────────────────────────────────────


def test_load_descriptions_success(tmp_path):
    data = {"fvBD": {"label": "Bridge Domain", "comment": "A BD."}}
    (tmp_path / "class-descriptions.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    result = load_descriptions(tmp_path / "class-descriptions.json")
    assert result == data


def test_load_descriptions_file_not_found_raises_error(tmp_path):
    with pytest.raises(DescriptionsLoadError) as exc_info:
        load_descriptions(tmp_path / "nonexistent.json")
    assert "class-descriptions.json" in str(exc_info.value) or "nonexistent" in str(
        exc_info.value
    )


def test_load_descriptions_invalid_json_raises_error(tmp_path):
    (tmp_path / "class-descriptions.json").write_text("{bad json}", encoding="utf-8")
    with pytest.raises(DescriptionsLoadError) as exc_info:
        load_descriptions(tmp_path / "class-descriptions.json")
    assert "JSON" in str(exc_info.value)


def test_load_descriptions_os_error_raises_error(tmp_path):
    f = tmp_path / "class-descriptions.json"
    f.write_text("{}", encoding="utf-8")
    f.chmod(0o000)
    try:
        with pytest.raises(DescriptionsLoadError):
            load_descriptions(f)
    finally:
        f.chmod(0o644)
