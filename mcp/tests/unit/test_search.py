# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Unit tests for registry.descriptions.search and load_descriptions (v2 scoring)."""

import json

import pytest
from exceptions import DescriptionsLoadError
from registry.descriptions import load_descriptions, search

_DESCRIPTIONS = {
    "fvBD": {
        "label": "Bridge Domain",
        "comment": "A bridge domain is a unique layer 2 forwarding domain.",
        "prop_labels": ["ARP Flooding", "Unicast Routing", "MAC Address", "MTU Size"],
        "isConfigurable": True,
    },
    "fvTenant": {
        "label": "Tenant",
        "comment": "A policy owner in the virtual fabric.",
        "isConfigurable": True,
    },
    "faultInst": {
        "comment": "Contains detailed information of a fault instance.",
    },
    "fvAEPg": {
        "label": "Application EPG",
        "comment": "A set of requirements for the application-level EPG.",
        "isConfigurable": True,
    },
    "vzBrCP": {
        "label": "Contract",
        "comment": "A contract governs communication between EPGs.",
        "isConfigurable": True,
    },
    "fvCtx": {
        "label": "VRF",
        "comment": "A VRF instance defines a layer 3 address domain.",
        "prop_labels": ["Data Plane Learning", "Policy Control Enforcement"],
        "isConfigurable": True,
    },
    # fvRsSvcBDToBDAtt: shares label "Bridge Domain" with fvBD — must not outrank it.
    "fvRsSvcBDToBDAtt": {
        "label": "Bridge Domain",
        "comment": "A source relation to the bridge domain.",
    },
    # fvRsVrfPol: "vrf" in class name too — still a relation class, still penalized.
    "fvRsVrfPol": {
        "label": "VRF Policy",
        "comment": "A relation to a VRF.",
    },
    # Non-configurable operational/stats class sharing fvBD's concept, to verify
    # the isConfigurable prior and stats-suffix penalty separate it from fvBD.
    "eqptcapacityBDEntry5min": {
        "label": "Bridge Domain",
        "comment": "A 5-minute capacity sample for a bridge domain.",
    },
    # Abstract class — can never be the right answer to "which class do I query".
    "nwItem": {
        "label": "Network Item",
        "comment": "An abstract network item.",
        "isAbstract": True,
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
    assert results[0]["class_name"] == "fvBD"


def test_partial_match_in_class_name():
    results = search("fv", _DESCRIPTIONS)
    class_names = [r["class_name"] for r in results]
    assert "fvBD" in class_names
    assert "fvTenant" in class_names


def test_multi_word_query_matches_camel_case_class_name():
    # "fabric node" cannot match a single unbroken substring of "fabricNode" —
    # this is the core capability v1's raw substring search lacked.
    descs = {
        "fabricNode": {"label": "Fabric Node", "comment": "The root node."},
        "fvTenant": {"label": "Tenant", "comment": "A policy owner."},
    }
    results = search("fabric node", descs)
    assert results[0]["class_name"] == "fabricNode"


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


def test_exact_label_match_ranks_first():
    # fvCtx's label is exactly "VRF" — an exact label match should dominate
    # any partial/incidental match elsewhere in the registry.
    results = search("VRF", _DESCRIPTIONS)
    assert results[0]["class_name"] == "fvCtx"


def test_label_match_ranks_higher_than_comment_only():
    results = search("Contract", _DESCRIPTIONS)
    assert results[0]["class_name"] == "vzBrCP"


def test_full_label_coverage_ranks_above_partial_token_overlap():
    # "bridge domain" fully covers fvBD's label tokens; a class that only
    # shares one of the two words should rank below it.
    descs = {
        "fvBD": {"label": "Bridge Domain", "comment": "", "isConfigurable": True},
        "fvBridgeUnrelated": {"label": "Bridge Something Else", "comment": ""},
    }
    results = search("bridge domain", descs)
    assert results[0]["class_name"] == "fvBD"


def test_configurable_class_ranks_above_non_configurable_same_label():
    # eqptcapacityBDEntry5min shares fvBD's exact label but is a non-
    # configurable stats class — fvBD must rank first.
    results = search("bridge domain", _DESCRIPTIONS)
    names = [r["class_name"] for r in results]
    assert names.index("fvBD") < names.index("eqptcapacityBDEntry5min")


def test_stats_suffix_class_ranks_below_primary_class():
    results = search("bridge domain", _DESCRIPTIONS)
    names = [r["class_name"] for r in results]
    assert "eqptcapacityBDEntry5min" in names
    assert names.index("fvBD") < names.index("eqptcapacityBDEntry5min")


def test_abstract_class_ranks_below_concrete_match_at_equal_coverage():
    # Neither label is an exact match for the query — the exact-match bonus
    # (which legitimately dominates everything else) is deliberately kept out
    # of play here, so the isAbstract penalty is what decides the tie between
    # two otherwise-equal token-coverage matches.
    descs = {
        "nwItem": {
            "label": "Network Item Base",
            "comment": "An abstract network item.",
            "isAbstract": True,
        },
        "networkItemConcrete": {
            "label": "Network Item Impl",
            "comment": "A concrete network item.",
            "isConfigurable": True,
        },
    }
    results = search("network item", descs)
    names = [r["class_name"] for r in results]
    assert names.index("networkItemConcrete") < names.index("nwItem")


# ── search() — edge cases ─────────────────────────────────────────────────────


def test_empty_keyword_returns_empty():
    results = search("", _DESCRIPTIONS)
    assert results == []


def test_whitespace_only_keyword_returns_empty():
    results = search("   ", _DESCRIPTIONS)
    assert results == []


def test_class_without_label_returns_empty_string_for_label():
    results = search("fault", _DESCRIPTIONS)
    fault = next((r for r in results if r["class_name"] == "faultInst"), None)
    assert fault is not None
    assert fault["label"] == ""


def test_metadata_with_none_values_does_not_crash():
    # A schema entry with an explicit null label/comment (as opposed to the
    # key being absent) still crashes — documents the gap for a future fix,
    # same as under the v1 implementation.
    descriptions_with_none = {
        "fvBD": {"label": None, "comment": None},
    }
    with pytest.raises((AttributeError, TypeError)):
        search("fvBD", descriptions_with_none)


def test_limit_zero_clamped_to_one():
    results = search("bridge", _DESCRIPTIONS, limit=0)
    assert len(results) == 1


def test_limit_negative_clamped_to_one():
    results = search("bridge", _DESCRIPTIONS, limit=-1)
    assert len(results) == 1


def test_limit_one_returns_exactly_one():
    results = search("fv", _DESCRIPTIONS, limit=1)
    assert len(results) == 1


def test_very_large_limit_returns_all_matches():
    results = search("a", _DESCRIPTIONS, limit=100_000)
    names = [r["class_name"] for r in results]
    for cls in ("fvBD", "fvTenant", "faultInst", "fvAEPg", "vzBrCP", "fvCtx"):
        assert cls in names


def test_search_is_deterministic_across_repeated_calls():
    # Ties are broken by (fewer name tokens, shorter name, alphabetical), so
    # identical input must always produce identical output — no reliance on
    # dict-iteration order or unstable sort behaviour.
    first = search("a", _DESCRIPTIONS, limit=100_000)
    second = search("a", _DESCRIPTIONS, limit=100_000)
    assert first == second


# ── Rs/Rt penalty ─────────────────────────────────────────────────────────────


def test_rs_class_ranks_below_primary_class_with_same_label():
    results = search("bridge domain", _DESCRIPTIONS)
    names = [r["class_name"] for r in results]
    assert "fvBD" in names
    assert "fvRsSvcBDToBDAtt" in names
    assert names.index("fvBD") < names.index("fvRsSvcBDToBDAtt")


def test_rs_class_ranks_below_primary_class_for_shared_token():
    results = search("vrf", _DESCRIPTIONS)
    names = [r["class_name"] for r in results]
    assert "fvCtx" in names
    assert "fvRsVrfPol" in names
    assert names.index("fvCtx") < names.index("fvRsVrfPol")


def test_rs_rt_pattern_with_numeric_package_prefix():
    # l3extRtVrfValidationPol — package prefix contains a digit; must still be caught.
    descs = {
        "fvCtx": {"label": "VRF", "comment": "Layer 3 network context.", "isConfigurable": True},
        "l3extRtVrfValidationPol": {"label": "VRF", "comment": "Validation policy."},
    }
    results = search("VRF", descs)
    names = [r["class_name"] for r in results]
    assert names.index("fvCtx") < names.index("l3extRtVrfValidationPol")


# ── prop_labels ────────────────────────────────────────────────────────────────


def test_prop_label_phrase_match_returns_class():
    # "arp flooding" is a prop_label of fvBD, not in its name/label/comment.
    results = search("arp flooding", _DESCRIPTIONS)
    assert results[0]["class_name"] == "fvBD"


def test_prop_label_match_does_not_outrank_exact_label_match():
    # fvCtx's label is exactly "VRF" and it also has no prop_label containing
    # "vrf" as a phrase — the exact label match must still win over any class
    # that only matches via prop_labels.
    descs = dict(_DESCRIPTIONS)
    descs["someOtherClass"] = {
        "label": "Other",
        "comment": "",
        "prop_labels": ["VRF Reference"],
    }
    results = search("vrf", descs)
    assert results[0]["class_name"] == "fvCtx"


def test_prop_label_multi_word_phrase_match():
    results = search("data plane learning", _DESCRIPTIONS)
    assert results[0]["class_name"] == "fvCtx"


def test_prop_label_search_case_insensitive():
    results = search("ARP FLOODING", _DESCRIPTIONS)
    assert results[0]["class_name"] == "fvBD"


def test_class_without_prop_labels_still_searchable():
    results = search("tenant", _DESCRIPTIONS)
    assert any(r["class_name"] == "fvTenant" for r in results)


def test_multiple_prop_labels_all_searchable_not_just_first():
    # Every prop_label must be reachable, not just the first one in the list —
    # v1's "break after first match" optimization no longer applies since v2
    # scores the whole joined haystack in one substring check.
    descs = {
        "fvBD": {
            "label": "Bridge Domain",
            "comment": "",
            "prop_labels": ["ARP Flooding", "Unicast Routing", "MAC Address", "MTU Size"],
            "isConfigurable": True,
        },
    }
    for phrase in ("arp flooding", "unicast routing", "mac address", "mtu size"):
        results = search(phrase, descs)
        assert results and results[0]["class_name"] == "fvBD", (
            f"expected fvBD for {phrase!r}, got {results}"
        )


# ── Structural priors in isolation ────────────────────────────────────────────


def test_isconfigurable_flag_absent_defaults_to_no_boost():
    # A class with no isConfigurable key at all must not error and must not
    # receive the configurable boost.
    descs = {"fvFoo": {"label": "Foo Bar", "comment": ""}}
    results = search("foo bar", descs)
    assert results[0]["class_name"] == "fvFoo"


def test_jargon_alias_surfaces_class_not_named_in_its_own_label():
    # bgpPeerP's real APIC label is "Peer Connectivity Profile" — it does not
    # contain "BGP" at all. The curated jargon table exists for exactly this.
    results = search("bgp peer policy", {
        "bgpPeerP": {
            "label": "Peer Connectivity Profile",
            "comment": "A BGP peer connectivity profile.",
            "isConfigurable": True,
        },
    })
    assert results[0]["class_name"] == "bgpPeerP"


# ── Index caching (identity-keyed, see _get_index) ───────────────────────────


def test_search_results_consistent_across_calls_with_same_dict():
    a = search("bridge", _DESCRIPTIONS)
    b = search("bridge", _DESCRIPTIONS)
    assert a == b


def test_search_rebuilds_index_for_a_distinct_dict_object():
    # A fresh dict with different content must not reuse a cached index built
    # for a different (even if structurally similar) dict object.
    descs_a = {"fvBD": {"label": "Bridge Domain", "comment": ""}}
    descs_b = {"fvBD": {"label": "Something Else Entirely", "comment": ""}}
    search("bridge", descs_a)  # warms the cache for descs_a
    results = search("something else entirely", descs_b)
    assert results and results[0]["class_name"] == "fvBD"


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
