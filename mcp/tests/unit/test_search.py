"""Unit tests for registry.descriptions.search."""

from registry.descriptions import search

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
}


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


def test_no_match_returns_empty_list():
    results = search("zzz_nonexistent_xyz", _DESCRIPTIONS)
    assert results == []


def test_limit_respected():
    results = search("a", _DESCRIPTIONS, limit=2)
    assert len(results) <= 2


def test_default_limit_is_10():
    # All 5 entries match "a" — should still return all 5 (< default 10)
    results = search("a", _DESCRIPTIONS)
    assert len(results) <= 10


def test_result_has_required_fields():
    results = search("tenant", _DESCRIPTIONS)
    assert len(results) > 0
    for r in results:
        assert "class_name" in r
        assert "label" in r
        assert "comment" in r


def test_class_name_match_ranks_higher_than_comment_match():
    # "fvBD" matches class name (weight 3) and label (weight 2)
    # "fvAEPg" only matches comment via "domain" → lower rank
    results = search("fvBD", _DESCRIPTIONS)
    assert results[0]["class_name"] == "fvBD"


def test_class_without_label_returns_empty_label():
    results = search("fault", _DESCRIPTIONS)
    fault = next((r for r in results if r["class_name"] == "faultInst"), None)
    assert fault is not None
    assert fault["label"] == ""
